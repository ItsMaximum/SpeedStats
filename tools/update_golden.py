"""Regenerate tests/fixtures/expected-*.csv from the fixture crawl. Run after an intentional scoring change:

    uv run python tools/update_golden.py

Then review the diff of the golden files - it shows exactly how every row's place/value moved - and commit them
together with the formula change.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb

from scraper.fixture import build_fixture_db
from speedstats import paths

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def main() -> None:
    data_dir = Path(tempfile.mkdtemp())
    build_fixture_db(data_dir, version="golden")
    con = duckdb.connect(str(paths.read_current(data_dir)), read_only=True)
    con.execute(
        f"""COPY (SELECT leaderboard, player, place, value, platform, date, flag FROM runs
                  ORDER BY leaderboard, place, player)
            TO '{(FIXTURES / "expected-runs.csv").as_posix()}' (HEADER, DELIMITER ',')"""
    )
    con.execute(
        f"""COPY (SELECT rank, player, round(points, 2) AS points FROM player_ranks ORDER BY rank)
            TO '{(FIXTURES / "expected-ranks.csv").as_posix()}' (HEADER, DELIMITER ',')"""
    )
    rows, boards = con.execute("SELECT row_count, leaderboard_count FROM meta").fetchone()
    con.close()
    print(f"golden files updated: {rows} rows, {boards} leaderboards - review the diff before committing")


if __name__ == "__main__":
    main()
