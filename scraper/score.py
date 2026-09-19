"""Scoring: turn the normalized intermediate tables in a work database into a published database file."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import duckdb

from speedstats import paths

log = logging.getLogger("speedstats.score")


def sql_text(name: str) -> str:
    return resources.files("scraper.sql").joinpath(name).read_text(encoding="utf-8")


def new_version(at: datetime | None = None) -> str:
    """Data version string: crawl start time, lexically sortable."""
    return (at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def configure(
    con: duckdb.DuckDBPyConnection, memory_limit: str = "6GB", threads: int = 4, tmp: Path | None = None
) -> None:
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"SET threads = {threads}")
    if tmp is not None:
        tmp.mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory = '{tmp.as_posix()}'")


def _unused_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 1
    while (candidate := path.with_name(f"{path.stem}.{n}{path.suffix}")).exists():
        n += 1
    return candidate


def build_published(
    con: duckdb.DuckDBPyConnection,
    out_path: Path,
    *,
    data_version: str,
    scraped_at: datetime,
    excluded_players: Iterable[str] = (),
    errored_games: int = 0,
) -> Path:
    """Run score.sql against the intermediate tables in `con`, writing `out_path` atomically.

    `con` must already hold games_d, series_d, game_series_d, platforms_d, areas_d, players_d and scored_input.
    """
    out_path = _unused_path(out_path)  # never overwrite: the API may be serving the old file (locked on Windows)
    partial = out_path.with_name(out_path.name + ".partial")
    if partial.exists():
        partial.unlink()

    con.execute("CREATE OR REPLACE TABLE excluded_players (name VARCHAR)")
    if rows := [(name,) for name in excluded_players]:
        con.executemany("INSERT INTO excluded_players VALUES (?)", rows)
    con.execute("CREATE OR REPLACE TABLE score_params (key VARCHAR, value VARCHAR)")
    con.executemany(
        "INSERT INTO score_params VALUES (?, ?)",
        [
            ("data_version", data_version),
            ("scraped_at", scraped_at.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")),
            ("errored_games", str(errored_games)),
        ],
    )

    log.info("scoring -> %s", out_path.name)
    con.execute(f"ATTACH '{partial.as_posix()}' AS pub")
    try:
        con.execute(sql_text("score.sql"))
        meta = con.execute("SELECT row_count, leaderboard_count, player_count, game_count FROM pub.meta").fetchone()
        log.info("scored: %s rows, %s leaderboards, %s players, %s games", *meta)
    finally:
        con.execute("DETACH pub")
    for table in ("lb", "lb_stats", "lb_valued"):
        con.execute(f"DROP TABLE IF EXISTS {table}")

    os.replace(partial, out_path)
    return out_path


def crawl_version(crawl: Path) -> str:
    return crawl.stem.removeprefix("crawl-")


def score_crawl(crawl: Path, data_dir: Path, *, excluded_players: Iterable[str], set_current: bool) -> Path:
    """Normalize a finished crawl database and score it into data_dir/speedstats-<version>.duckdb."""
    version = crawl_version(crawl)
    con = duckdb.connect(str(crawl))
    configure(con, tmp=paths.work_dir(data_dir) / "tmp")
    try:
        stage = con.execute("SELECT value FROM crawl_meta WHERE key = 'stage'").fetchone()
        if not stage or stage[0] != "games_crawled":
            raise SystemExit(f"crawl {crawl.name} is not complete (stage: {stage[0] if stage else 'none'})")
        started = con.execute("SELECT value FROM crawl_meta WHERE key = 'started_at'").fetchone()
        scraped_at = datetime.fromisoformat(started[0]).replace(tzinfo=UTC) if started else datetime.now(UTC)
        errored = con.execute("SELECT COUNT(*) FROM crawl_checkpoint WHERE status = 'error'").fetchone()[0]
        log.info("normalizing %s", crawl.name)
        con.execute(sql_text("normalize.sql"))
        out = build_published(
            con,
            paths.published_path(data_dir, version),
            data_version=version,
            scraped_at=scraped_at,
            excluded_players=excluded_players,
            errored_games=errored,
        )
    finally:
        con.close()
    if set_current:
        paths.write_current(data_dir, out)
        log.info("CURRENT -> %s", out.name)
    return out
