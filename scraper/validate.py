"""Sanity gates a freshly scored database must pass before it goes live. Any failure keeps the old data serving."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

log = logging.getLogger("speedstats.validate")

SMOKE_GAME = "Red Ball"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Report:
    version: str
    checks: list[Check]
    stats: dict[str, object]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def summary(self) -> str:
        lines = [f"validation of {self.version}: {'PASS' if self.ok else 'FAIL'}"]
        lines += [f"  [{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in self.checks]
        return "\n".join(lines)

    def write(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "version": self.version,
                    "ok": self.ok,
                    "checks": [asdict(c) for c in self.checks],
                    "stats": self.stats,
                },
                indent=1,
                default=str,
            ),
            encoding="utf-8",
        )


def validate(new: Path, previous: Path | None, min_leaderboards: int, excluded_players: list[str]) -> Report:
    con = duckdb.connect(str(new), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    meta = _meta(con)
    checks: list[Check] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append(Check(name, bool(ok), detail))

    check(
        "leaderboards",
        meta["leaderboard_count"] >= min_leaderboards,
        f"{meta['leaderboard_count']} >= {min_leaderboards}",
    )
    check(
        "errored games",
        meta["errored_games"] <= 25 and meta["errored_games"] <= 0.001 * meta["game_count"],
        f"{meta['errored_games']} of {meta['game_count']} games",
    )

    nulls = con.execute(
        "SELECT COUNT(*) FROM runs WHERE player IS NULL OR leaderboard IS NULL OR game IS NULL OR value IS NULL"
    ).fetchone()[0]
    check("no null keys", nulls == 0, f"{nulls} rows with nulls")
    bad = con.execute("SELECT COUNT(*) FROM runs WHERE value < 0 OR place < 1").fetchone()[0]
    check("values and places sane", bad == 0, f"{bad} bad rows")
    dupes = con.execute("SELECT COUNT(*) - COUNT(DISTINCT (leaderboard_id, player_id)) FROM runs").fetchone()[0]
    check("one credit per player per leaderboard", dupes == 0, f"{dupes} duplicates")
    excluded = con.execute(
        "SELECT COUNT(*) FROM runs WHERE player IN (SELECT unnest($p::VARCHAR[]))", {"p": excluded_players}
    ).fetchone()[0]
    check("excluded players absent", excluded == 0, f"{excluded} rows")

    for request in ("games", "players"):
        n = con.execute(
            "SELECT COUNT(*) FROM runs WHERE game = $g"
            if request == "games"
            else "SELECT COUNT(*) FROM player_ranks WHERE player_id IN (SELECT player_id FROM runs WHERE game = $g)",
            {"g": SMOKE_GAME},
        ).fetchone()[0]
        check(f"smoke: {SMOKE_GAME} {request}", n > 0, f"{n} rows")

    stats: dict[str, object] = dict(meta)
    if previous is not None and previous.exists():
        con.execute(f"ATTACH '{previous.as_posix()}' AS prev (READ_ONLY)")
        prev = _meta(con, "prev")
        total_new = con.execute("SELECT SUM(value) FROM runs").fetchone()[0]
        total_prev = con.execute("SELECT SUM(value) FROM prev.runs").fetchone()[0]
        overlap = con.execute(
            """SELECT COUNT(*) FROM (SELECT player_id FROM player_ranks ORDER BY rank LIMIT 100) n
               JOIN (SELECT player_id FROM prev.player_ranks ORDER BY rank LIMIT 100) p USING (player_id)"""
        ).fetchone()[0]
        for key, floor in (("row_count", 0.90), ("game_count", 0.95), ("player_count", 0.90)):
            check(f"{key} vs previous", meta[key] >= floor * prev[key], f"{meta[key]} vs {prev[key]} (>= {floor:.0%})")
        ratio = total_new / total_prev if total_prev else 1.0
        check("total value vs previous", 0.85 <= ratio <= 1.15, f"{ratio:.3f}")
        check("top-100 overlap", overlap >= 70, f"{overlap}/100")
        stats.update(
            {
                "previous_version": prev["data_version"],
                "value_ratio": ratio,
                "top100_overlap": overlap,
                "row_delta": meta["row_count"] - prev["row_count"],
            }
        )
    else:
        check("previous version", True, "none to compare against")
    con.close()
    return Report(str(meta["data_version"]), checks, stats)


def _meta(con: duckdb.DuckDBPyConnection, schema: str = "main") -> dict[str, object]:
    cur = con.execute(f"SELECT * FROM {schema}.meta")
    return dict(zip([d[0] for d in cur.description], cur.fetchone(), strict=True))
