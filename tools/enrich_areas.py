"""Dev helper: give a published database (built from a legacy runs.json, which has no player areas) real flags for
the top N players, using the speedrun.com API directly. Writes a new version and points CURRENT at it.

    uv run python tools/enrich_areas.py --top 100
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
from pathlib import Path

import duckdb
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from speedstats import paths  # noqa: E402
from speedstats.config import settings  # noqa: E402

API = "https://www.speedrun.com/api/v2/"
HEADERS = {"Accept": "application/json", "Accept-Language": "en", "User-Agent": "SpeedStats/4 (speedstats.app)"}


def api(client: httpx.Client, endpoint: str, **params):
    r = base64.urlsafe_b64encode(json.dumps(params, separators=(",", ":")).encode()).decode().rstrip("=")
    resp = client.get(API + endpoint, params={"_r": r})
    resp.raise_for_status()
    return resp.json()


def flag_of(area: dict) -> str:
    return area["lbFlagIcon"].removeprefix("/images/flags/").removesuffix(".png")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=100)
    p.add_argument("--data-dir", default=str(settings.data_dir))
    args = p.parse_args()
    data_dir = Path(args.data_dir)

    current = paths.read_current(data_dir)
    if current is None:
        print("no CURRENT database")
        return 1
    target = data_dir / (current.stem.split("+")[0] + "+areas.duckdb")
    shutil.copy(current, target)

    with httpx.Client(timeout=60, headers=HEADERS) as client:
        areas = api(client, "GetStaticData")["areas"]
        con = duckdb.connect(str(target))
        top = [r[0] for r in con.execute(f"SELECT player FROM player_ranks ORDER BY rank LIMIT {args.top}").fetchall()]
        found: dict[str, str] = {}
        for name in top:
            try:
                found[name] = api(client, "GetUserSummary", url=name)["user"].get("areaId") or ""
            except httpx.HTTPError as e:
                print("  skip", name, e)
    by_id = {a["id"]: a for a in areas}

    con.execute("DELETE FROM areas")
    con.executemany(
        "INSERT INTO areas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                a["id"],
                a["name"],
                a["fullName"],
                a["lbName"],
                flag_of(a),
                a.get("parentId"),
                flag_of(a) == a["id"],
                a["id"].lower(),
                a["name"].lower(),
                a["lbName"].lower(),
            )
            for a in areas
        ],
    )
    updates = [
        (area, area.split("/")[0], flag_of(by_id[area]), name) for name, area in found.items() if area and area in by_id
    ]
    if updates:
        con.executemany("UPDATE players SET area_id = ?, country = ?, flag = ? WHERE name = ?", updates)
        for table in ("runs", "player_ranks"):
            con.executemany(f"UPDATE {table} SET country = ?, flag = ? WHERE player = ?", [u[1:] for u in updates])
    con.close()
    paths.write_current(data_dir, target)
    print(f"{len(areas)} areas, {len(updates)} of {len(top)} players given flags -> {target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
