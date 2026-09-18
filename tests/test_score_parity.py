"""Proves the DuckDB scoring port reproduces the old SpeedStats-V3 Python pipeline.

tests/fixtures/test-runs.json is a small crawl in the old format; test-runs.csv is what the old
processruns.py produced from it. The same harness runs locally against the
full runs.json/runs.csv pair via `python -m scraper parity`.
"""

from pathlib import Path

import duckdb
import pytest

from scraper.parity import compare_to_csv, format_report
from speedstats import paths

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def published(fixture_data_dir: Path) -> Path:
    return paths.read_current(fixture_data_dir)


def test_parity_with_old_pipeline(published: Path):
    report = compare_to_csv(published, FIXTURES / "test-runs.csv")
    assert report["ok"], format_report(report)
    assert report["matched"] == report["csv_rows"] == report["db_rows"]
    # values agree to the 3 printed decimals, not just within tolerance
    assert report["checks"]["value_string_mismatch"]["count"] == 0, format_report(report)
    # the fixture CSV was generated west of UTC, so its dates trail the true (UTC) dates by one day
    assert report["checks"]["date_beyond_1_day"]["count"] == 0


def test_meta_and_player_ranks(published: Path):
    con = duckdb.connect(str(published), read_only=True)
    meta = con.execute("SELECT data_version, row_count, leaderboard_count, player_count FROM meta").fetchone()
    assert meta[0] == "test" and meta[1] == 8994 and meta[2] > 800 and meta[3] > 1000
    top = con.execute("SELECT rank, player, points FROM player_ranks ORDER BY rank LIMIT 3").fetchall()
    assert [r[0] for r in top] == [1, 2, 3]
    assert top[0][2] >= top[1][2] >= top[2][2]
    # the +1e7 IGT sentinel and reverse-time categories produce sane places
    assert con.execute("SELECT MIN(place), MIN(value) FROM runs").fetchone() == (1, 0.0)
    con.close()


def test_excluded_players_are_dropped(tmp_path):
    """EXCLUDED_PLAYERS removes a player's rows but keeps their share of co-op values (denominator unchanged)."""
    from datetime import UTC, datetime

    from scraper.legacy import load_legacy_json
    from scraper.score import build_published, configure

    con = duckdb.connect(str(tmp_path / "work.duckdb"))
    configure(con, memory_limit="1GB", threads=2)
    load_legacy_json(con, FIXTURES / "test-runs.json")
    out = build_published(
        con,
        tmp_path / "x.duckdb",
        data_version="x",
        scraped_at=datetime(2025, 9, 4, tzinfo=UTC),
        excluded_players=["NorXor"],
    )
    con.close()
    pub = duckdb.connect(str(out), read_only=True)
    assert pub.execute("SELECT COUNT(*) FROM runs WHERE player = 'NorXor'").fetchone()[0] == 0
    assert pub.execute("SELECT COUNT(*) FROM player_ranks WHERE player = 'NorXor'").fetchone()[0] == 0
    assert pub.execute("SELECT row_count FROM meta").fetchone()[0] < 8994
    pub.close()
