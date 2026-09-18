"""Regression check of the new API against the live PHP site, for real production URLs.

    uv run python tools/regression.py capture              # fetch each URL in tests/regression/urls.txt from speedstats.app
    uv run python tools/regression.py check --db <file>    # compare snapshots with the new API over a full-data database

Only columns present in both are compared (the new API adds Country). Differences in data (the live site may be on
an older crawl than the runs.json used locally) show up as row differences; differences in logic show up as
place/points differences for the same rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
URLS = ROOT / "tests" / "regression" / "urls.txt"
SNAPSHOTS = ROOT / "tests" / "regression" / "snapshots"
PROD = "https://speedstats.app/index.php?"


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.columns: list[str] = []
        self.rows: list[list[str]] = []
        self._cell: str | None = None
        self._row: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("th", "td"):
            self._cell = ""
        elif tag == "tr":
            self._row = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data

    def handle_endtag(self, tag):
        if tag == "th":
            self.columns.append(self._cell.strip())
            self._cell = None
        elif tag == "td":
            self._row.append(self._cell.strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def urls() -> list[str]:
    return [
        line.strip()
        for line in URLS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def snapshot_path(qs: str) -> Path:
    return SNAPSHOTS / (hashlib.sha1(qs.encode()).hexdigest()[:12] + ".json")


def capture() -> None:
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120) as client:
        for qs in urls():
            r = client.get(PROD + qs)
            r.raise_for_status()
            parser = TableParser()
            parser.feed(r.text)
            snapshot_path(qs).write_text(
                json.dumps({"query": qs, "columns": parser.columns, "rows": parser.rows}, indent=1), encoding="utf-8"
            )
            print(f"{len(parser.rows):5d} rows  {qs}")


def _normalize(value: str | int | float | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, str):
        try:
            return _normalize(float(value)) if "." in value else value
        except ValueError:
            return value
    return str(value)


def check(db: Path) -> int:
    from fastapi.testclient import TestClient

    from api import main
    from speedstats import paths

    data_dir = db.parent
    if paths.read_current(data_dir) != db:
        paths.write_current(data_dir, db)
    main.holder.data_dir = data_dir
    failures = 0
    with TestClient(main.app) as client:
        for qs in urls():
            path = snapshot_path(qs)
            if not path.exists():
                print(f"MISSING snapshot  {qs}")
                continue
            snap = json.loads(path.read_text(encoding="utf-8"))
            out = client.get("/api/query?" + qs).json()
            common = [c for c in snap["columns"] if c in out["columns"]]
            si = [snap["columns"].index(c) for c in common]
            ai = [out["columns"].index(c) for c in common]
            prod = [tuple(_normalize(r[i]) for i in si) for r in snap["rows"]]
            new = [tuple(_normalize(r[i]) for i in ai) for r in out["rows"]]
            if prod == new:
                print(f"OK    {len(new):5d} rows  {qs}")
                continue
            failures += 1
            only_prod = [r for r in prod if r not in set(new)]
            only_new = [r for r in new if r not in set(prod)]
            print(f"DIFF  prod={len(prod)} new={len(new)} only_prod={len(only_prod)} only_new={len(only_new)}  {qs}")
            for r in only_prod[:3]:
                print("        prod:", r)
            for r in only_new[:3]:
                print("        new: ", r)
            if out["warnings"]:
                print("        warnings:", out["warnings"])
    print(f"\n{failures} of {len(urls())} queries differ")
    return 1 if failures else 0


def main_() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capture")
    c = sub.add_parser("check")
    c.add_argument("--db", required=True)
    args = p.parse_args()
    if args.cmd == "capture":
        capture()
        return 0
    return check(Path(args.db).resolve())


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main_())
