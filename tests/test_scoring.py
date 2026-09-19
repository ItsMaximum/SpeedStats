"""Scoring regression: the fixture crawl (Fancy Pants series) must score exactly as recorded in the golden files.

The golden files were generated from a crawl that was verified, row by row, to reproduce the original
SpeedStats-V3 Python pipeline (same leaderboards, places and 3-decimal values). Any change in score.sql or
normalize.sql that alters a value shows up here.

After an intentional formula change: `uv run python tools/update_golden.py`, review the diff, commit both.
"""

from pathlib import Path

import duckdb
import pytest

from scraper.fixture import build_fixture_db
from speedstats import paths

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def con(fixture_data_dir: Path):
    c = duckdb.connect(str(paths.read_current(fixture_data_dir)), read_only=True)
    yield c
    c.close()


def test_runs_match_golden_file(con):
    diff = con.execute(
        f"""
        WITH expected AS (SELECT * FROM read_csv('{(FIXTURES / "expected-runs.csv").as_posix()}', header = true)),
        actual AS (SELECT leaderboard, player, place, value, platform, date, flag FROM runs)
        SELECT COUNT(*) FROM (
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        )
        """
    ).fetchone()[0]
    assert diff == 0
    assert con.execute("SELECT row_count, leaderboard_count, game_count FROM meta").fetchone() == (1077, 136, 13)


def test_player_ranks_match_golden_file(con):
    diff = con.execute(
        f"""
        WITH expected AS (SELECT * FROM read_csv('{(FIXTURES / "expected-ranks.csv").as_posix()}', header = true)),
        actual AS (SELECT rank, player, round(points, 2) AS points FROM player_ranks)
        SELECT COUNT(*) FROM ((SELECT * FROM expected EXCEPT SELECT * FROM actual)
                              UNION ALL (SELECT * FROM actual EXCEPT SELECT * FROM expected))
        """
    ).fetchone()[0]
    assert diff == 0


def test_lookup_tables(con):
    assert con.execute("SELECT name, slug FROM games WHERE slug = 'fpa1'").fetchone() == (
        "The Fancy Pants Adventures: World 1",
        "fpa1",
    )
    assert con.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 1  # only series with games are kept
    assert con.execute("SELECT is_country, lb_name FROM areas WHERE id = 'gb/eng'").fetchone() == (True, "England")
    assert con.execute("SELECT is_country FROM areas WHERE id = 'us/ca'").fetchone() == (False,)
    # players carry speedrun.com's leaderboard flag and dark-mode name colours
    flag, color1 = con.execute("SELECT flag, color1 FROM players WHERE name = 'DylCat'").fetchone()
    assert flag == "us" and color1.startswith("#")


def test_excluded_players_are_dropped(tmp_path):
    out = build_fixture_db(tmp_path, version="x", excluded_players=["DylCat"])
    pub = duckdb.connect(str(out), read_only=True)
    assert pub.execute("SELECT COUNT(*) FROM runs WHERE player = 'DylCat'").fetchone()[0] == 0
    assert pub.execute("SELECT COUNT(*) FROM player_ranks WHERE player = 'DylCat'").fetchone()[0] == 0
    assert pub.execute("SELECT row_count FROM meta").fetchone()[0] < 1077
    pub.close()
