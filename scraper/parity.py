"""Compare a published database against a runs.csv produced by the old SpeedStats-V3 pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

CSV_COLUMNS = ["Leaderboard", "Series", "Game", "Player", "Platform", "Place", "Value", "Date"]


def compare_to_csv(published: Path, csv_path: Path, samples: int = 5) -> dict[str, Any]:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"ATTACH '{published.as_posix()}' AS pub (READ_ONLY)")
    # The old generateCSV doubled backslashes in Leaderboard/Game (for LOAD DATA) and wrote \N for nulls.
    con.execute(
        f"""
        CREATE TABLE csv AS
        SELECT replace(Leaderboard, '\\\\', '\\') AS leaderboard, Player AS player, Platform AS platform,
               Place::INTEGER AS place, Value::DOUBLE AS value, Value AS value_str, Date::DATE AS date
        FROM read_csv('{csv_path.as_posix()}', header = false, names = {CSV_COLUMNS!r}, quote = '"',
                      escape = '"', nullstr = '\\N', all_varchar = true)
        """
    )
    con.execute(
        """
        CREATE TABLE joined AS
        SELECT coalesce(c.leaderboard, d.leaderboard) AS leaderboard, coalesce(c.player, d.player) AS player,
               c.leaderboard IS NOT NULL AS in_csv, d.leaderboard IS NOT NULL AS in_db,
               c.place AS csv_place, d.place AS db_place,
               c.value AS csv_value, d.value AS db_value, c.value_str AS csv_value_str,
               printf('%.3f', d.value) AS db_value_str,
               c.platform AS csv_platform, d.platform AS db_platform,
               c.date AS csv_date, d.date AS db_date
        FROM csv c FULL OUTER JOIN pub.runs d ON d.leaderboard = c.leaderboard AND d.player = c.player
        """
    )

    def count(where: str) -> int:
        return con.execute(f"SELECT COUNT(*) FROM joined WHERE {where}").fetchone()[0]

    def sample(where: str) -> list[tuple]:
        return con.execute(
            f"SELECT leaderboard, player, csv_place, db_place, csv_value_str, db_value_str, csv_platform, db_platform, "
            f"csv_date, db_date FROM joined WHERE {where} LIMIT {samples}"
        ).fetchall()

    checks = {
        "only_in_csv": "in_csv AND NOT in_db",
        "only_in_db": "in_db AND NOT in_csv",
        "place_mismatch": "in_csv AND in_db AND csv_place IS DISTINCT FROM db_place",
        "value_beyond_tolerance": "in_csv AND in_db AND abs(csv_value - db_value) > 0.0015",
        "value_string_mismatch": "in_csv AND in_db AND csv_value_str <> db_value_str",
        "platform_mismatch": "in_csv AND in_db AND csv_platform IS DISTINCT FROM db_platform",
        "date_beyond_1_day": "in_csv AND in_db AND abs(date_diff('day', csv_date, db_date)) > 1",
        "date_not_exact": "in_csv AND in_db AND csv_date IS DISTINCT FROM db_date",
    }
    report: dict[str, Any] = {
        "csv_rows": con.execute("SELECT COUNT(*) FROM csv").fetchone()[0],
        "db_rows": con.execute("SELECT COUNT(*) FROM pub.runs").fetchone()[0],
        "matched": count("in_csv AND in_db"),
        "checks": {},
    }
    for name, where in checks.items():
        n = count(where)
        report["checks"][name] = {"count": n, "samples": sample(where) if n else []}
    hard = (
        "only_in_csv",
        "only_in_db",
        "place_mismatch",
        "value_beyond_tolerance",
        "platform_mismatch",
        "date_beyond_1_day",
    )
    report["ok"] = all(report["checks"][name]["count"] == 0 for name in hard)
    con.close()
    return report


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"csv rows: {report['csv_rows']}   db rows: {report['db_rows']}   matched: {report['matched']}",
        f"parity: {'OK' if report['ok'] else 'FAILED'}",
    ]
    for name, check in report["checks"].items():
        lines.append(f"  {name}: {check['count']}")
        for row in check["samples"]:
            lines.append(f"      {row}")
    return "\n".join(lines)
