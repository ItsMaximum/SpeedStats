"""Holds the read-only DuckDB connection to the live database and hot-swaps it when CURRENT changes."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from speedstats import paths

log = logging.getLogger("speedstats.api.db")

STALE_AFTER = timedelta(days=9)
CLOSE_OLD_AFTER = 60.0
POLL_SECONDS = 10.0


@dataclass(frozen=True)
class Meta:
    data_version: str
    scraped_at: datetime
    published_at: datetime
    row_count: int
    leaderboard_count: int
    player_count: int
    game_count: int

    @property
    def stale(self) -> bool:
        return datetime.now(UTC) - self.scraped_at > STALE_AFTER


class NoDatabase(RuntimeError):
    pass


_on_reload: list = []


def on_reload(callback) -> None:
    """Register a callback run after a new database file is opened (e.g. to drop cached results)."""
    _on_reload.append(callback)


def cache_clear() -> None:
    for cb in _on_reload:
        cb()


class DbHolder:
    def __init__(self, data_dir: Path, threads: int = 4):
        self.data_dir = data_dir
        self.threads = threads
        self._lock = threading.Lock()
        self._con: duckdb.DuckDBPyConnection | None = None
        self._path: Path | None = None
        self._stamp: tuple[float, int] | None = None  # (mtime, size) of the open file
        self._meta: Meta | None = None

    # -- lifecycle -----------------------------------------------------------------------------------------------

    def open(self) -> bool:
        """Open whatever CURRENT points at. Returns False when nothing is published yet."""
        path = paths.read_current(self.data_dir)
        if path is None:
            log.warning("no published database in %s", self.data_dir)
            return False
        stat = path.stat()
        stamp = (stat.st_mtime, stat.st_size)
        if path == self._path and stamp == self._stamp:
            return True
        con = duckdb.connect(str(path), read_only=True)
        con.execute("SET TimeZone = 'UTC'")
        con.execute(f"SET threads = {self.threads}")
        meta = _read_meta(con)
        with self._lock:
            old, self._con, self._path, self._stamp, self._meta = self._con, con, path, stamp, meta
        cache_clear()
        log.info("opened %s (data version %s, %s rows)", path.name, meta.data_version, meta.row_count)
        if old is not None:
            threading.Timer(CLOSE_OLD_AFTER, old.close).start()
        return True

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
            self._con = self._path = self._stamp = self._meta = None

    async def watch(self) -> None:
        """Poll CURRENT and reopen on change. Runs for the life of the app."""
        while True:
            await asyncio.sleep(POLL_SECONDS)
            try:
                await asyncio.to_thread(self.open)
            except Exception:  # keep serving the old file
                log.exception("failed to open new database")

    # -- access --------------------------------------------------------------------------------------------------

    @property
    def meta(self) -> Meta:
        if self._meta is None:
            raise NoDatabase("no database loaded")
        return self._meta

    @property
    def ready(self) -> bool:
        return self._con is not None

    def cursor(self) -> duckdb.DuckDBPyConnection:
        """A per-call cursor; DuckDB connections must not be shared across threads concurrently."""
        with self._lock:
            if self._con is None:
                raise NoDatabase("no database loaded")
            return self._con.cursor()


def _read_meta(con: duckdb.DuckDBPyConnection) -> Meta:
    row = con.execute(
        "SELECT data_version, scraped_at, published_at, row_count, leaderboard_count, player_count, game_count "
        "FROM meta"
    ).fetchone()
    return Meta(
        data_version=row[0],
        scraped_at=row[1].replace(tzinfo=UTC),
        published_at=row[2].replace(tzinfo=UTC),
        row_count=row[3],
        leaderboard_count=row[4],
        player_count=row[5],
        game_count=row[6],
    )
