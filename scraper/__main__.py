"""SpeedStats scraper CLI: `python -m scraper <command>`."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import duckdb

from speedstats import paths
from speedstats.config import settings

FIXTURE_JSON = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "test-runs.json"


def _score_from_json(json_path: Path, data_dir: Path, version: str | None, set_current: bool) -> Path:
    from scraper.legacy import load_legacy_json
    from scraper.score import build_published, configure, new_version

    version = version or new_version()
    work = paths.work_dir(data_dir) / f"legacy-{version}.duckdb"
    if work.exists():
        work.unlink()
    con = duckdb.connect(str(work))
    configure(con, tmp=paths.work_dir(data_dir) / "tmp")
    try:
        scraped_at = load_legacy_json(con, json_path)
        out = build_published(
            con,
            paths.published_path(data_dir, version),
            data_version=version,
            scraped_at=scraped_at,
            excluded_players=settings.excluded_players,
        )
    finally:
        con.close()
    work.unlink(missing_ok=True)
    if set_current:
        paths.write_current(data_dir, out)
        logging.info("CURRENT -> %s", out.name)
    return out


def cmd_score(args: argparse.Namespace) -> int:
    if args.from_json is None:
        logging.error("only --from-json is implemented yet (crawl scoring arrives with the crawler)")
        return 2
    _score_from_json(Path(args.from_json), Path(args.data_dir), args.version, not args.no_current)
    return 0


def cmd_fixture_db(args: argparse.Namespace) -> int:
    _score_from_json(FIXTURE_JSON, Path(args.data_dir), "fixture", True)
    return 0


def cmd_parity(args: argparse.Namespace) -> int:
    from scraper.parity import compare_to_csv, format_report

    published = Path(args.db) if args.db else paths.read_current(Path(args.data_dir))
    if published is None:
        logging.error("no published database; pass --db or run score first")
        return 2
    report = compare_to_csv(published, Path(args.csv))
    print(format_report(report))
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scraper")
    parser.add_argument("--data-dir", default=str(settings.data_dir))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("score", help="score a crawl (or a legacy runs.json) into a published database")
    p.add_argument("--from-json", help="path to a SpeedStats-V3 runs.json")
    p.add_argument("--version", help="data version (default: now)")
    p.add_argument("--no-current", action="store_true", help="do not point CURRENT at the result")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("fixture-db", help="build a small database from tests/fixtures for local development")
    p.set_defaults(func=cmd_fixture_db)

    p = sub.add_parser("parity", help="compare a published database against an old-pipeline runs.csv")
    p.add_argument("--csv", required=True)
    p.add_argument("--db", help="published database (default: CURRENT)")
    p.set_defaults(func=cmd_parity)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    started = time.time()
    code = args.func(args)
    logging.info("finished in %.1fs", time.time() - started)
    return code


if __name__ == "__main__":
    sys.exit(main())
