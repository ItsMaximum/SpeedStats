"""SpeedStats scraper CLI: `python -m scraper <command>`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import UTC, datetime
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


def _latest_crawl(data_dir: Path) -> Path | None:
    crawls = sorted(paths.work_dir(data_dir).glob("crawl-*.duckdb"))
    return crawls[-1] if crawls else None


def _crawl_version(path: Path) -> str:
    return path.stem.removeprefix("crawl-")


async def _run_crawl(path: Path, cfg, resume: bool) -> None:
    from scraper.crawl import Crawler
    from scraper.proxy_pool import ProxyPool
    from scraper.src_client import SrcClient, dedupe_proxies_by_ip
    from scraper.writer import DbWriter

    apps = await dedupe_proxies_by_ip(settings.src_proxies) if settings.use_proxy else []
    pool = ProxyPool.build(apps, settings.src_per_proxy_concurrency)
    logging.info("crawling via %s (%d concurrent requests)", "proxies" if apps else "direct requests", pool.capacity)
    client = SrcClient(pool, settings.src_max_attempts, settings.src_timeout)
    writer = DbWriter(path)
    if not resume:
        writer.set_meta("started_at", datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "))
    writer.set_meta("version", _crawl_version(path))
    writer.start()
    try:
        await Crawler(client, writer, cfg).run()
    finally:
        await writer.close()
        await client.aclose()


def cmd_crawl(args: argparse.Namespace) -> int:
    from scraper.crawl import CrawlConfig

    data_dir = Path(args.data_dir)
    if args.resume and not args.version:
        latest = _latest_crawl(data_dir)
        if latest is None:
            logging.error("nothing to resume in %s", paths.work_dir(data_dir))
            return 2
        path = latest
    else:
        from scraper.score import new_version

        path = paths.crawl_path(data_dir, args.version or new_version())
    cfg = CrawlConfig(
        excluded_games=set(settings.excluded_games),
        excluded_categories=set(settings.excluded_categories),
        games_in_flight=settings.games_in_flight,
        only_series=args.only_series,
        only_game=args.only_game,
    )
    logging.info("crawl file: %s (resume=%s)", path, args.resume)
    asyncio.run(_run_crawl(path, cfg, args.resume))
    return 0


def _score_crawl(crawl: Path, data_dir: Path, set_current: bool) -> Path:
    from scraper.score import build_published, configure, sql_text

    version = _crawl_version(crawl)
    con = duckdb.connect(str(crawl))
    configure(con, tmp=paths.work_dir(data_dir) / "tmp")
    try:
        stage = con.execute("SELECT value FROM crawl_meta WHERE key = 'stage'").fetchone()
        if not stage or stage[0] != "games_crawled":
            raise SystemExit(f"crawl {crawl.name} is not complete (stage: {stage[0] if stage else 'none'})")
        started = con.execute("SELECT value FROM crawl_meta WHERE key = 'started_at'").fetchone()
        scraped_at = datetime.fromisoformat(started[0]).replace(tzinfo=UTC) if started else datetime.now(UTC)
        errored = con.execute("SELECT COUNT(*) FROM crawl_checkpoint WHERE status = 'error'").fetchone()[0]
        logging.info("normalizing %s", crawl.name)
        con.execute(sql_text("normalize.sql"))
        out = build_published(
            con,
            paths.published_path(data_dir, version),
            data_version=version,
            scraped_at=scraped_at,
            excluded_players=settings.excluded_players,
            errored_games=errored,
        )
    finally:
        con.close()
    if set_current:
        paths.write_current(data_dir, out)
        logging.info("CURRENT -> %s", out.name)
    return out


def cmd_score(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    if args.from_json:
        _score_from_json(Path(args.from_json), data_dir, args.version, not args.no_current)
        return 0
    crawl = Path(args.crawl) if args.crawl else _latest_crawl(data_dir)
    if crawl is None:
        logging.error("no crawl file found; pass --crawl or --from-json")
        return 2
    _score_crawl(crawl, data_dir, not args.no_current)
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

    p = sub.add_parser("crawl", help="crawl speedrun.com into work/crawl-<version>.duckdb")
    p.add_argument("--resume", action="store_true", help="continue the latest (or --version) crawl file")
    p.add_argument("--version", help="crawl version to create or resume")
    p.add_argument("--only-series", metavar="ID", help="crawl only the games of one series")
    p.add_argument("--only-game", metavar="ID", help="crawl only one game")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("score", help="score a crawl (or a legacy runs.json) into a published database")
    p.add_argument("--crawl", help="crawl file (default: latest in work/)")
    p.add_argument("--from-json", help="path to a SpeedStats-V3 runs.json instead of a crawl")
    p.add_argument("--version", help="data version for --from-json (default: now)")
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
    logging.getLogger("httpx").setLevel(logging.WARNING)
    started = time.time()
    code = args.func(args)
    logging.info("finished in %.1fs", time.time() - started)
    return code


if __name__ == "__main__":
    sys.exit(main())
