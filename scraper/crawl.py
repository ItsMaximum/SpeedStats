"""The crawl: series list -> series games -> all games -> per game: data + every leaderboard page.

Everything is appended to the crawl database as it arrives (bounded memory); progress is checkpointed per game
so a crashed or cancelled crawl resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from scraper.src_client import CrawlError, LeaderboardPage, NotFound, SrcClient
from scraper.writer import DbWriter, now

log = logging.getLogger("speedstats.crawl")

STAGES = ["", "static", "series_listed", "series_games_listed", "games_listed", "games_crawled"]
LEADERBOARD_TYPE = 1  # 1 = GetGameLeaderboard (200 runs/page), 2 = GetGameLeaderboard2 (100 runs/page)
# Series whose v2 GetGameList(seriesId) is incomplete (Harry Potter: 4 of 39 games); use the v1 API for these.
V1_SERIES = {"15ndxp7r"}


@dataclass
class CrawlConfig:
    excluded_games: set[str] = field(default_factory=set)
    excluded_categories: set[str] = field(default_factory=set)
    games_in_flight: int = 48
    only_series: str | None = None
    only_game: str | None = None


@dataclass
class GameResult:
    categories: int = 0
    pages: int = 0
    runs: int = 0


def flag_of(area: dict[str, Any]) -> str:
    return area["lbFlagIcon"].removeprefix("/images/flags/").removesuffix(".png")


class Crawler:
    def __init__(self, client: SrcClient, writer: DbWriter, cfg: CrawlConfig):
        self.client = client
        self.writer = writer
        self.cfg = cfg
        self.game_sem = asyncio.Semaphore(cfg.games_in_flight)
        self.done = 0
        self.total = 0
        self.started = time.monotonic()

    # -- stage bookkeeping ---------------------------------------------------------------------------------------

    def stage_reached(self, stage: str) -> bool:
        current = self.writer.get_meta("stage") or ""
        return STAGES.index(current) >= STAGES.index(stage)

    async def finish_stage(self, stage: str) -> None:
        await self.writer.drain()
        self.writer.set_meta("stage", stage)
        log.info("stage complete: %s", stage)

    # -- stages --------------------------------------------------------------------------------------------------

    async def run(self) -> None:
        if not self.stage_reached("static"):
            await self.stage_static()
            await self.finish_stage("static")
        if self.cfg.only_game:
            game_ids = [self.cfg.only_game]
        else:
            if not self.stage_reached("series_listed"):
                await self.stage_series()
                await self.finish_stage("series_listed")
            if not self.stage_reached("series_games_listed"):
                await self.stage_series_games()
                await self.finish_stage("series_games_listed")
            if not self.cfg.only_series and not self.stage_reached("games_listed"):
                await self.stage_all_games()
                await self.finish_stage("games_listed")
            game_ids = self.pending_games()
        await self.stage_games(game_ids)
        await self.finish_stage("games_crawled")

    async def stage_static(self) -> None:
        data = await self.client.get_static_data()
        seen = now()
        await self.writer.put(
            "raw_areas",
            [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "full_name": a.get("fullName"),
                    "lb_name": a.get("lbName") or a["name"],
                    "lb_flag": flag_of(a),
                    "parent_id": a.get("parentId"),
                    "seen_at": seen,
                }
                for a in data["areas"]
            ],
        )
        await self.writer.put(
            "raw_colors",
            [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "dark": c.get("darkColor"),
                    "light": c.get("lightColor"),
                    "seen_at": seen,
                }
                for c in data.get("colors", [])
            ],
        )
        log.info("static data: %d areas, %d colors", len(data["areas"]), len(data.get("colors", [])))

    async def stage_series(self) -> None:
        first = await self.client.get_series_list(1)
        pages = first["pagination"]["pages"]
        await self._put_series(first["seriesList"])
        rest = await asyncio.gather(*(self.client.get_series_list(p) for p in range(2, pages + 1)))
        for page in rest:
            await self._put_series(page["seriesList"])
        log.info("series list: %d pages", pages)

    async def _put_series(self, series: list[dict]) -> None:
        seen = now()
        await self.writer.put(
            "raw_series",
            [{"id": s["id"], "name": s["name"].strip(), "url": s.get("url"), "seen_at": seen} for s in series],
        )

    async def stage_series_games(self) -> None:
        await self.writer.drain()
        rows = self.writer.con.execute("SELECT DISTINCT id FROM raw_series").fetchall()
        series_ids = [r[0] for r in rows]
        if self.cfg.only_series:
            if self.cfg.only_series not in series_ids:
                raise CrawlError(f"series {self.cfg.only_series} not found in the series list")
            series_ids = [self.cfg.only_series]
        self.total = len(series_ids)
        self.done = 0
        await asyncio.gather(*(self._series_games(sid) for sid in series_ids))
        log.info("series games listed for %d series", len(series_ids))

    async def _series_games(self, series_id: str) -> None:
        async with self.game_sem:
            if series_id in V1_SERIES:
                await self._series_games_v1(series_id)
            else:
                page = 1
                while True:
                    data = await self.client.get_game_list(page, series_id)
                    await self._put_game_list(data["gameList"], series_id)
                    if page >= data["pagination"]["pages"]:
                        break
                    page += 1
            self._progress("series")

    async def _series_games_v1(self, series_id: str) -> None:
        offset = 0
        while True:
            data = await self.client.get_series_games_v1(series_id, offset)
            games = [
                {"id": g["id"], "name": g["names"]["international"], "url": g.get("abbreviation")}
                for g in data.get("data", [])
            ]
            await self._put_game_list(games, series_id)
            size = data.get("pagination", {}).get("size", len(games))
            if not games or size < 200:
                break
            offset += size

    async def stage_all_games(self) -> None:
        first = await self.client.get_game_list(1)
        pages = first["pagination"]["pages"]
        await self._put_game_list(first["gameList"])
        self.total, self.done = pages, 1

        async def fetch(p: int) -> None:
            async with self.game_sem:
                data = await self.client.get_game_list(p)
                await self._put_game_list(data["gameList"])
                self._progress("game list pages")

        await asyncio.gather(*(fetch(p) for p in range(2, pages + 1)))
        log.info("game list: %d pages", pages)

    async def _put_game_list(self, games: list[dict], series_id: str | None = None) -> None:
        seen = now()
        await self.writer.put(
            "raw_game_list",
            [{"id": g["id"], "name": g["name"].strip(), "url": g.get("url"), "seen_at": seen} for g in games],
        )
        if series_id:
            await self.writer.put(
                "raw_game_series", [{"game_id": g["id"], "series_id": series_id, "seen_at": seen} for g in games]
            )

    def pending_games(self) -> list[str]:
        con = self.writer.con
        rows = con.execute(
            """
            SELECT DISTINCT id FROM raw_game_list
            WHERE id NOT IN (SELECT game_id FROM crawl_checkpoint WHERE status IN ('done', 'skipped'))
            ORDER BY id
            """
        ).fetchall()
        todo = [r[0] for r in rows if r[0] not in self.cfg.excluded_games]
        # partial data of unfinished games is re-fetched from scratch
        con.execute(
            "DELETE FROM raw_runs WHERE game_id NOT IN (SELECT game_id FROM crawl_checkpoint WHERE status = 'done')"
        )
        return todo

    async def stage_games(self, game_ids: list[str]) -> None:
        self.total, self.done = len(game_ids), 0
        self.started = time.monotonic()
        log.info("crawling %d games", len(game_ids))
        await asyncio.gather(*(self.crawl_game(gid) for gid in game_ids))
        await self.writer.drain()

        errored = [
            r[0]
            for r in self.writer.con.execute("SELECT game_id FROM crawl_checkpoint WHERE status = 'error'").fetchall()
        ]
        if errored:
            log.warning("retrying %d errored games", len(errored))
            self.writer.con.execute(
                "DELETE FROM raw_runs WHERE game_id IN (SELECT game_id FROM crawl_checkpoint WHERE status = 'error')"
            )
            self.total, self.done = len(errored), 0
            await asyncio.gather(*(self.crawl_game(gid) for gid in errored))
            await self.writer.drain()
        log.info("proxies: %s", self.client.pool.stats())

    # -- per game --------------------------------------------------------------------------------------------------

    async def crawl_game(self, game_id: str) -> None:
        async with self.game_sem:
            result = GameResult()
            try:
                data = await self.client.get_game_data(game_id)
            except NotFound:
                await self._checkpoint(game_id, "skipped", result, "not found")
                return
            except CrawlError as e:
                await self._checkpoint(game_id, "error", result, self.client.pool.redact(str(e)))
                return
            try:
                await self._put_game_data(game_id, data)
                categories = [c for c in data["categories"] if c["id"] not in self.cfg.excluded_categories]
                result.categories = len(categories)
                outcomes = await asyncio.gather(
                    *(self.crawl_category(game_id, c["id"]) for c in categories), return_exceptions=True
                )
            except Exception as e:  # malformed payload etc.
                await self._checkpoint(game_id, "error", result, self.client.pool.redact(f"{type(e).__name__}: {e}"))
                return
            errors = [o for o in outcomes if isinstance(o, BaseException)]
            for o in outcomes:
                if isinstance(o, tuple):
                    result.pages += o[0]
                    result.runs += o[1]
            if errors:
                detail = self.client.pool.redact(f"{type(errors[0]).__name__}: {errors[0]}")
                await self._checkpoint(game_id, "error", result, detail)
            else:
                await self._checkpoint(game_id, "done", result)
            self._progress("games")

    async def _put_game_data(self, game_id: str, data: dict[str, Any]) -> None:
        seen = now()
        game = data["game"]
        w = self.writer
        await w.put(
            "raw_games",
            [
                {
                    "id": game_id,
                    "name": game["name"].strip(),
                    "url": game.get("url"),
                    "default_timer": game.get("defaultTimer"),
                    "seen_at": seen,
                }
            ],
        )
        await w.put(
            "raw_categories",
            [
                {
                    "id": c["id"],
                    "game_id": game_id,
                    "name": c["name"].strip(),
                    "time_direction": c.get("timeDirection", 0),
                    "archived": bool(c.get("archived")),
                    "seen_at": seen,
                }
                for c in data["categories"]
            ],
        )
        await w.put(
            "raw_levels",
            [
                {"id": lv["id"], "game_id": game_id, "name": lv["name"].strip(), "seen_at": seen}
                for lv in data["levels"]
            ],
        )
        await w.put(
            "raw_variables",
            [
                {
                    "id": v["id"],
                    "game_id": game_id,
                    "name": v["name"].strip(),
                    "is_subcategory": bool(v.get("isSubcategory")),
                    "archived": bool(v.get("archived")),
                    "seen_at": seen,
                }
                for v in data["variables"]
            ],
        )
        await w.put(
            "raw_values",
            [
                {
                    "id": v["id"],
                    "variable_id": v["variableId"],
                    "game_id": game_id,
                    "name": v["name"].strip(),
                    "seen_at": seen,
                }
                for v in data["values"]
            ],
        )
        await w.put(
            "raw_platforms",
            [
                {"id": p["id"], "name": p["name"].strip(), "url": p.get("url"), "seen_at": seen}
                for p in data["platforms"]
            ],
        )

    async def crawl_category(self, game_id: str, category_id: str) -> tuple[int, int]:
        lb_type = LEADERBOARD_TYPE
        first = await self.client.get_leaderboard(game_id, category_id, 1, lb_type)
        runs = await self._put_page(first, lb_type, 1)
        rest = await asyncio.gather(
            *(self.client.get_leaderboard(game_id, category_id, p, lb_type) for p in range(2, first.pages + 1))
        )
        for p, page in enumerate(rest, start=2):
            runs += await self._put_page(page, lb_type, p)
        return max(first.pages, 1), runs

    async def _put_page(self, page: LeaderboardPage, lb_type: int, page_no: int) -> int:
        seen = now()
        await self.writer.put(
            "raw_players",
            [
                {
                    "id": p["id"],
                    "name": p["name"].strip(),
                    "url": p.get("url"),
                    "area_id": p.get("areaId") or None,
                    "color1_id": p.get("color1Id"),
                    "color2_id": p.get("color2Id"),
                    "seen_at": seen,
                }
                for p in page.players
            ],
        )
        rows = [
            {
                "ord": self.writer.next_ord(),
                "run_id": r["id"],
                "game_id": r["gameId"],
                "category_id": r["categoryId"],
                "level_id": r.get("levelId"),
                "value_ids": r.get("valueIds") or [],
                "player_ids": r.get("playerIds") or [],
                "platform_id": r.get("platformId"),
                "time": r.get("time"),
                "time_with_loads": r.get("timeWithLoads"),
                "igt": r.get("igt"),
                "date": r.get("date"),
                "date_submitted": r.get("dateSubmitted"),
                "lb_type": lb_type,
                "page": page_no,
            }
            for r in page.runs
        ]
        await self.writer.put("raw_runs", rows)
        return len(rows)

    async def _checkpoint(self, game_id: str, status: str, result: GameResult, error: str | None = None) -> None:
        if error:
            log.warning("game %s %s: %s", game_id, status, error)
        await self.writer.put(
            "crawl_checkpoint",
            [
                {
                    "game_id": game_id,
                    "status": status,
                    "categories": result.categories,
                    "pages": result.pages,
                    "runs": result.runs,
                    "error": error,
                    "finished_at": now(),
                }
            ],
        )

    def _progress(self, what: str) -> None:
        self.done += 1
        if self.done % 100 == 0 or self.done == self.total:
            elapsed = time.monotonic() - self.started
            rate = self.done / elapsed if elapsed else 0
            eta = (self.total - self.done) / rate if rate else 0
            log.info("%s: %d/%d (%.1f/s, eta %.0fs)", what, self.done, self.total, rate, eta)
