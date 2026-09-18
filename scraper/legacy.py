"""Load a SpeedStats-V3 `runs.json` (dict of leaderboard name -> list of run dicts) into the intermediate
tables, streaming so memory stays bounded. Ids are names in this mode (the old file has no ids).

Used for the scoring parity proof, for `fixture-db`, and for local development against real data
before the new crawler exists.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import ijson
import pyarrow as pa

from scraper.score import sql_text

log = logging.getLogger("speedstats.legacy")

GUEST_PREFIX = "[Guest]"
NO_DATE_SUBMITTED = 2147483647
BATCH_ROWS = 50_000

RUN_SCHEMA = pa.schema(
    [
        ("ord", pa.int64()),
        ("run_id", pa.string()),
        ("leaderboard_name", pa.string()),
        ("game_id", pa.string()),
        ("platform_id", pa.string()),
        ("player_ids", pa.list_(pa.string())),
        ("is_reverse", pa.bool_()),
        ("t", pa.float64()),
        ("date", pa.int64()),
        ("date_submitted", pa.int64()),
        ("is_level_run", pa.bool_()),
    ]
)


def _groups(path: Path) -> Iterator[tuple[str, list[dict]]]:
    with path.open("rb") as f:
        yield from ijson.kvitems(f, "", use_float=True)


def insert_arrow(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict], schema: pa.Schema) -> None:
    if not rows:
        return
    batch = pa.Table.from_pylist(rows, schema=schema)
    con.register("_batch", batch)
    try:
        con.execute(f"INSERT INTO {table} SELECT * FROM _batch")
    finally:
        con.unregister("_batch")


def _insert(con: duckdb.DuckDBPyConnection, table: str, columns: list[str], rows: list[tuple]) -> None:
    """Bulk insert via Arrow; executemany would issue one statement per row."""
    if not rows:
        return
    data = {col: [r[i] for r in rows] for i, col in enumerate(columns)}
    con.register("_batch", pa.table(data))
    try:
        con.execute(f"INSERT INTO {table} ({', '.join(columns)}) SELECT * FROM _batch")
    finally:
        con.unregister("_batch")


def load_legacy_json(con: duckdb.DuckDBPyConnection, path: Path) -> datetime:
    """Populate the intermediate tables from `path`. Returns the file's mtime as the scrape time."""
    con.execute(sql_text("intermediate_schema.sql"))

    games: dict[str, set[str]] = {}  # game name -> series names
    platforms: set[str] = set()
    players: set[str] = set()
    rows: list[dict] = []
    ord_ = 0
    n_groups = 0

    for leaderboard_name, runs in _groups(path):
        n_groups += 1
        for run in runs:
            game = run["gameName"]
            series = run.get("seriesName")
            games.setdefault(game, set())
            if series is not None:
                games[game].add(series)
            platform = run.get("platformName")
            if platform is not None:
                platforms.add(platform)
            names = run["playerNames"]
            players.update(name for name in names if name is not None)

            date = run.get("date")
            submitted = run.get("dateSubmitted")
            rows.append(
                {
                    "ord": ord_,
                    "run_id": str(ord_),
                    "leaderboard_name": leaderboard_name,
                    "game_id": game,
                    "platform_id": platform,
                    "player_ids": names,
                    "is_reverse": bool(run["isReverseTime"]),
                    "t": run.get("time"),
                    "date": int(date) if date else 0,
                    "date_submitted": int(submitted) if submitted is not None else NO_DATE_SUBMITTED,
                    "is_level_run": bool(run["isLevelRun"]),
                }
            )
            ord_ += 1
        if len(rows) >= BATCH_ROWS:
            insert_arrow(con, "scored_input", rows, RUN_SCHEMA)
            rows = []
            if n_groups % 100_000 == 0:
                log.info("loaded %s groups, %s runs", n_groups, ord_)
    insert_arrow(con, "scored_input", rows, RUN_SCHEMA)
    log.info("loaded %s groups, %s runs", n_groups, ord_)

    series = sorted({s for names in games.values() for s in names})
    _insert(con, "games_d", ["id", "name"], [(g, g) for g in games])
    _insert(con, "series_d", ["id", "name"], [(s, s) for s in series])
    _insert(con, "game_series_d", ["game_id", "series_id"], [(g, s) for g, ns in games.items() for s in sorted(ns)])
    _insert(con, "platforms_d", ["id", "name"], [(p, p) for p in sorted(platforms)])
    _insert(con, "players_d", ["id", "name", "is_guest"], [(p, p, p.startswith(GUEST_PREFIX)) for p in players])
    # no area data in the legacy format: country / flag stay NULL
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
