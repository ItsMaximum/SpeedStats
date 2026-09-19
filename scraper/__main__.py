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

from scraper.score import crawl_version, score_crawl
from speedstats import paths
from speedstats.config import settings


def _latest_crawl(data_dir: Path) -> Path | None:
    crawls = sorted(paths.work_dir(data_dir).glob("crawl-*.duckdb"))
    return crawls[-1] if crawls else None


def _unfinished_crawl(data_dir: Path) -> Path | None:
    """The latest crawl file that has not reached the final stage (a finished crawl is never resumed)."""
    latest = _latest_crawl(data_dir)
    if latest is None:
        return None
    con = duckdb.connect(str(latest), read_only=True)
    try:
        row = con.execute("SELECT value FROM crawl_meta WHERE key = 'stage'").fetchone()
    finally:
        con.close()
    return None if row and row[0] == "games_crawled" else latest


async def _run_crawl(path: Path, cfg, resume: bool) -> None:
    from scraper.crawl import Crawler
    from scraper.proxy_pool import ProxyPool
    from scraper.src_client import SrcClient, dedupe_proxies_by_ip
    from scraper.writer import DbWriter

    apps = await dedupe_proxies_by_ip(settings.src_proxies) if settings.use_proxy else []
    pool = ProxyPool.build(
        apps, settings.src_per_proxy_concurrency, settings.src_window_requests, settings.src_window_seconds
    )
    logging.info(
        "crawling via %s: %d concurrent requests, %d requests per %.0f-minute window",
        "proxies" if apps else "direct requests",
        pool.capacity,
        pool.budget_per_window,
        settings.src_window_seconds / 60,
    )
    client = SrcClient(pool, settings.src_max_attempts, settings.src_timeout)
    writer = DbWriter(path)
    if not resume:
        writer.set_meta("started_at", datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "))
    writer.set_meta("version", crawl_version(path))
    writer.start()
    try:
        await Crawler(client, writer, cfg).run()
    finally:
        await writer.close()
        await client.aclose()


def cmd_crawl(args: argparse.Namespace) -> int:
    from scraper.crawl import CrawlConfig
    from scraper.score import new_version

    data_dir = Path(args.data_dir)
    resume = False
    if args.version:
        path = paths.crawl_path(data_dir, args.version)
        resume = args.resume and path.exists()
    elif args.resume and (unfinished := _unfinished_crawl(data_dir)) is not None:
        path, resume = unfinished, True  # continue the unfinished crawl
    else:
        path = paths.crawl_path(data_dir, new_version())  # nothing to resume: start fresh
    cfg = CrawlConfig(
        excluded_games=set(settings.excluded_games),
        excluded_categories=set(settings.excluded_categories),
        games_in_flight=settings.games_in_flight,
        only_series=args.only_series,
        only_game=args.only_game,
    )
    logging.info("crawl file: %s (resume=%s)", path, resume)
    asyncio.run(_run_crawl(path, cfg, resume))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    crawl = Path(args.crawl) if args.crawl else _latest_crawl(data_dir)
    if crawl is None:
        logging.error("no crawl file found; pass --crawl")
        return 2
    score_crawl(crawl, data_dir, excluded_players=settings.excluded_players, set_current=not args.no_current)
    return 0


def _validate(new: Path, data_dir: Path):
    from scraper.validate import validate

    previous = paths.read_current(data_dir)
    if previous is not None and previous.resolve() == new.resolve():
        previous = None
    report = validate(new, previous, settings.min_leaderboards, settings.excluded_players)
    report.write(data_dir / f"validation-{report.version}.json")
    print(report.summary())
    return report


def cmd_validate(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    new = Path(args.db) if args.db else (paths.list_published(data_dir) or [None])[-1]
    if new is None:
        logging.error("no published database to validate")
        return 2
    return 0 if _validate(new, data_dir).ok else 1


def cmd_publish(args: argparse.Namespace) -> int:
    from scraper.publish import publish

    data_dir = Path(args.data_dir)
    new = Path(args.db) if args.db else (paths.list_published(data_dir) or [None])[-1]
    if new is None:
        logging.error("no published database")
        return 2
    if not args.skip_validation and not _validate(new, data_dir).ok:
        logging.error("validation failed; not publishing %s", new.name)
        return 1
    publish(new, settings)
    return 0


def _live_data_age_days(data_dir: Path) -> float | None:
    current = paths.read_current(data_dir)
    if current is None:
        return None
    con = duckdb.connect(str(current), read_only=True)
    try:
        scraped_at = con.execute("SELECT scraped_at FROM meta").fetchone()[0]
    finally:
        con.close()
    return (datetime.now(UTC) - scraped_at.replace(tzinfo=UTC)).total_seconds() / 86400


def cmd_all(args: argparse.Namespace) -> int:
    """crawl -> score -> validate -> publish, the weekly job."""
    from scraper.publish import publish

    data_dir = Path(args.data_dir)
    args.only_series = args.only_game = None
    # Do not start a new crawl if the live data is fresh (e.g. a manual run a few days before the Sunday schedule);
    # an unfinished crawl is still resumed, and --force overrides.
    age = _live_data_age_days(data_dir)
    resuming = args.resume and _unfinished_crawl(data_dir) is not None
    if not args.force and not args.version and not resuming and age is not None:
        if age < settings.min_crawl_interval_days:
            msg = f"live data is {age:.1f} days old (< {settings.min_crawl_interval_days:g}); not crawling again"
            logging.info(msg)
            _append_job_summary(f"## Scrape skipped\n\n{msg}. Use *Run workflow* with **force** to crawl anyway.\n")
            return 0
    if cmd_crawl(args):
        return 1
    crawl = _latest_crawl(data_dir)
    assert crawl is not None
    new = score_crawl(crawl, data_dir, excluded_players=settings.excluded_players, set_current=False)
    report = _validate(new, data_dir)
    _write_job_summary(report)
    if not report.ok:
        logging.error("validation failed; %s stays unpublished", new.name)
        return 1
    publish(new, settings)
    return 0


def _append_job_summary(text: str) -> None:
    """GitHub Actions shows $GITHUB_STEP_SUMMARY on the run page."""
    import os

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def _write_job_summary(report) -> None:
    s = report.stats
    lines = [
        f"## Scrape {report.version}: {'PASS' if report.ok else 'FAIL'}",
        "",
        f"- rows: {s.get('row_count')} (delta {s.get('row_delta', 'n/a')})",
        f"- leaderboards: {s.get('leaderboard_count')}, players: {s.get('player_count')}, games: {s.get('game_count')}",
        f"- errored games: {s.get('errored_games')}",
        f"- total value vs previous: {s.get('value_ratio', 'n/a')}, top-100 overlap: {s.get('top100_overlap', 'n/a')}",
        "",
        "| check | result | detail |",
        "|---|---|---|",
    ]
    lines += [f"| {c.name} | {'ok' if c.ok else 'FAIL'} | {c.detail} |" for c in report.checks]
    _append_job_summary("\n".join(lines) + "\n")


def cmd_bootstrap(args: argparse.Namespace) -> int:
    from scraper.publish import bootstrap_from_r2

    bootstrap_from_r2(settings)
    return 0


def cmd_fixture_db(args: argparse.Namespace) -> int:
    from scraper.fixture import build_fixture_db

    build_fixture_db(Path(args.data_dir), excluded_players=settings.excluded_players)
    return 0


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

    p = sub.add_parser("score", help="score a finished crawl into a published database")
    p.add_argument("--crawl", help="crawl file (default: latest in work/)")
    p.add_argument("--no-current", action="store_true", help="do not point CURRENT at the result")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("validate", help="run the sanity gates on a scored database")
    p.add_argument("--db", help="database file (default: newest published)")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("publish", help="make a scored database live (CURRENT, cache purge, R2 snapshot)")
    p.add_argument("--db", help="database file (default: newest published)")
    p.add_argument("--skip-validation", action="store_true")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("all", help="crawl, score, validate and publish (the weekly job)")
    p.add_argument("--resume", action="store_true", help="continue the latest crawl file if one exists")
    p.add_argument("--version", help="crawl version to create or resume")
    p.add_argument(
        "--force", action="store_true", help="crawl even if the live data is younger than the minimum interval"
    )
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("bootstrap", help="download the latest published database from R2")
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("fixture-db", help="build a small database (the Fancy Pants series) for local development")
    p.set_defaults(func=cmd_fixture_db)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # logs show up promptly when redirected to a file
    started = time.time()
    code = args.func(args)
    logging.info("finished in %.1fs", time.time() - started)
    return code


if __name__ == "__main__":
    sys.exit(main())
