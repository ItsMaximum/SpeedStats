"""Make a validated database live: point CURRENT at it, wait for the API to pick it up, purge the edge cache,
snapshot to R2, prune old versions."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import duckdb
import httpx

from speedstats import paths
from speedstats.config import Settings

log = logging.getLogger("speedstats.publish")

SNAPSHOT_TABLES = ("runs", "player_ranks", "games", "series", "game_series", "platforms", "areas", "players", "meta")
KEEP_VERSIONS = 2


def publish(new: Path, settings: Settings) -> None:
    data_dir = settings.data_dir
    version = new.stem.removeprefix("speedstats-")
    paths.write_current(data_dir, new)
    log.info("CURRENT -> %s", new.name)

    if settings.api_health_url:
        wait_for_api(settings.api_health_url, version)
    if settings.cloudflare_zone_id and settings.cloudflare_api_token:
        purge_cloudflare(settings.cloudflare_zone_id, settings.cloudflare_api_token)
    else:
        log.info("Cloudflare purge skipped (not configured)")
    if settings.r2_bucket and settings.r2_account_id and settings.r2_access_key_id:
        snapshot_to_r2(new, version, settings)
    else:
        log.info("R2 snapshot skipped (not configured)")
    prune(data_dir, keep=KEEP_VERSIONS)


def wait_for_api(health_url: str, version: str, timeout: float = 120.0) -> bool:
    """The API polls CURRENT every 10s; wait until it reports the new version so the purge is not premature."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(health_url, timeout=10)
            if r.status_code == 200 and r.json().get("data_version") == version:
                log.info("API serves %s", version)
                return True
        except httpx.HTTPError:
            pass
        time.sleep(5)
    log.warning("API did not report %s within %.0fs (is it running?)", version, timeout)
    return False


def purge_cloudflare(zone_id: str, token: str) -> None:
    r = httpx.post(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache",
        headers={"Authorization": f"Bearer {token}"},
        json={"purge_everything": True},
        timeout=30,
    )
    if r.status_code == 200 and r.json().get("success"):
        log.info("Cloudflare cache purged")
    else:
        log.error("Cloudflare purge failed: %s %s", r.status_code, r.text[:300])


def snapshot_to_r2(db: Path, version: str, settings: Settings) -> None:
    """Parquet copies of every published table plus the database itself, at snapshots/<version>/ and latest/."""
    import boto3

    export = paths.work_dir(settings.data_dir) / f"snapshot-{version}"
    export.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db), read_only=True)
    try:
        for table in SNAPSHOT_TABLES:
            con.execute(
                f"COPY {table} TO '{(export / f'{table}.parquet').as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
    finally:
        con.close()

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )
    files = [(p, p.name) for p in export.glob("*.parquet")] + [(db, "speedstats.duckdb")]
    for path, name in files:
        for prefix in (f"snapshots/{version}/", "snapshots/latest/"):
            s3.upload_file(str(path), settings.r2_bucket, prefix + name)
    log.info("uploaded %d files to r2://%s/snapshots/%s/", len(files), settings.r2_bucket, version)
    for p in export.glob("*.parquet"):
        p.unlink()
    export.rmdir()


def bootstrap_from_r2(settings: Settings) -> Path:
    """Download the latest published database (for a fresh machine or local development)."""
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )
    target = settings.data_dir / "speedstats-bootstrap.duckdb"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    s3.download_file(settings.r2_bucket, "snapshots/latest/speedstats.duckdb", str(target))
    con = duckdb.connect(str(target), read_only=True)
    version = con.execute("SELECT data_version FROM meta").fetchone()[0]
    con.close()
    final = paths.published_path(settings.data_dir, version)
    target.replace(final)
    paths.write_current(settings.data_dir, final)
    log.info("bootstrapped %s from R2", final.name)
    return final


def prune(data_dir: Path, keep: int) -> None:
    """Delete published files older than the newest `keep`, never the live one."""
    current = paths.read_current(data_dir)
    published = paths.list_published(data_dir)
    for old in published[:-keep]:
        if old != current:
            old.unlink(missing_ok=True)
            log.info("pruned %s", old.name)
    for crawl in sorted(paths.work_dir(data_dir).glob("crawl-*.duckdb"))[:-1]:
        crawl.unlink(missing_ok=True)
        Path(str(crawl) + ".wal").unlink(missing_ok=True)
        log.info("pruned %s", crawl.name)
