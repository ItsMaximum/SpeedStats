"""A small real crawl checked in as one JSON file, for tests and local development"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa

from scraper.score import score_crawl, sql_text
from speedstats import paths

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "crawl.json"


def import_crawl(target: Path, fixture: Path = FIXTURE) -> Path:
    """Recreate a crawl database from the fixture: {table name: [row, ...]} for every raw table."""
    if target.exists():
        target.unlink()
    data = json.loads(fixture.read_text(encoding="utf-8"))
    con = duckdb.connect(str(target))
    try:
        con.execute(sql_text("raw_schema.sql"))
        for table, rows in data.items():
            if not rows:
                continue
            con.register("_batch", pa.Table.from_pylist(rows))  # timestamps are ISO strings; DuckDB casts them
            con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _batch")
            con.unregister("_batch")
    finally:
        con.close()
    return target


def build_fixture_db(data_dir: Path, version: str = "fixture", excluded_players: list[str] = ()) -> Path:
    """Import the fixture crawl, score it and point CURRENT at the result."""
    crawl = import_crawl(paths.crawl_path(data_dir, version))
    out = score_crawl(crawl, data_dir, excluded_players=list(excluded_players), set_current=True)
    crawl.unlink(missing_ok=True)
    return out
