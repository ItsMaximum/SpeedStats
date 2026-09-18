"""Single writer for the crawl database. Producers enqueue rows; a background task flushes them in batches,
each flush being one transaction in FIFO order, so a game's checkpoint row can never land before its runs."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from scraper.score import sql_text

log = logging.getLogger("speedstats.writer")

FLUSH_ROWS = 20_000
FLUSH_SECONDS = 2.0
CHECKPOINT_SECONDS = 300.0

SCHEMAS: dict[str, pa.Schema] = {
    "raw_series": pa.schema(
        [("id", pa.string()), ("name", pa.string()), ("url", pa.string()), ("seen_at", pa.timestamp("us"))]
    ),
    "raw_game_list": pa.schema(
        [("id", pa.string()), ("name", pa.string()), ("url", pa.string()), ("seen_at", pa.timestamp("us"))]
    ),
    "raw_game_series": pa.schema(
        [("game_id", pa.string()), ("series_id", pa.string()), ("seen_at", pa.timestamp("us"))]
    ),
    "raw_games": pa.schema(
        [
            ("id", pa.string()),
            ("name", pa.string()),
            ("url", pa.string()),
            ("default_timer", pa.int8()),
            ("seen_at", pa.timestamp("us")),
        ]
    ),
    "raw_categories": pa.schema(
        [
            ("id", pa.string()),
            ("game_id", pa.string()),
            ("name", pa.string()),
            ("time_direction", pa.int8()),
            ("archived", pa.bool_()),
            ("seen_at", pa.timestamp("us")),
        ]
    ),
    "raw_levels": pa.schema(
        [("id", pa.string()), ("game_id", pa.string()), ("name", pa.string()), ("seen_at", pa.timestamp("us"))]
    ),
    "raw_variables": pa.schema(
        [
            ("id", pa.string()),
            ("game_id", pa.string()),
            ("name", pa.string()),
            ("is_subcategory", pa.bool_()),
            ("archived", pa.bool_()),
            ("seen_at", pa.timestamp("us")),
        ]
    ),
    "raw_values": pa.schema(
        [
            ("id", pa.string()),
            ("variable_id", pa.string()),
            ("game_id", pa.string()),
            ("name", pa.string()),
            ("seen_at", pa.timestamp("us")),
        ]
    ),
    "raw_platforms": pa.schema(
        [("id", pa.string()), ("name", pa.string()), ("url", pa.string()), ("seen_at", pa.timestamp("us"))]
    ),
    "raw_areas": pa.schema(
        [
            ("id", pa.string()),
            ("name", pa.string()),
            ("full_name", pa.string()),
            ("lb_name", pa.string()),
            ("lb_flag", pa.string()),
            ("parent_id", pa.string()),
            ("seen_at", pa.timestamp("us")),
        ]
    ),
    "raw_colors": pa.schema(
        [
            ("id", pa.string()),
            ("name", pa.string()),
            ("dark", pa.string()),
            ("light", pa.string()),
            ("seen_at", pa.timestamp("us")),
        ]
    ),
    "raw_players": pa.schema(
        [
            ("id", pa.string()),
            ("name", pa.string()),
            ("url", pa.string()),
            ("area_id", pa.string()),
            ("color1_id", pa.string()),
            ("color2_id", pa.string()),
            ("seen_at", pa.timestamp("us")),
        ]
    ),
    "raw_runs": pa.schema(
        [
            ("ord", pa.int64()),
            ("run_id", pa.string()),
            ("game_id", pa.string()),
            ("category_id", pa.string()),
            ("level_id", pa.string()),
            ("value_ids", pa.list_(pa.string())),
            ("player_ids", pa.list_(pa.string())),
            ("platform_id", pa.string()),
            ("time", pa.float64()),
            ("time_with_loads", pa.float64()),
            ("igt", pa.float64()),
            ("date", pa.int64()),
            ("date_submitted", pa.int64()),
            ("lb_type", pa.int8()),
            ("page", pa.int32()),
        ]
    ),
    "crawl_checkpoint": pa.schema(
        [
            ("game_id", pa.string()),
            ("status", pa.string()),
            ("categories", pa.int32()),
            ("pages", pa.int32()),
            ("runs", pa.int32()),
            ("error", pa.string()),
            ("finished_at", pa.timestamp("us")),
        ]
    ),
}


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DbWriter:
    def __init__(self, path: Path, queue_size: int = 500):
        self.path = path
        self.con = duckdb.connect(str(path))
        self.con.execute(sql_text("raw_schema.sql"))
        self._queue: asyncio.Queue[tuple[str, list[dict]] | None] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task | None = None
        self._ord = self.con.execute("SELECT coalesce(max(ord), -1) + 1 FROM raw_runs").fetchone()[0]
        self.rows_written: dict[str, int] = defaultdict(int)

    def next_ord(self) -> int:
        self._ord += 1
        return self._ord - 1

    # -- sync helpers (setup / bookkeeping outside the flush loop) ---------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.con.execute("SELECT value FROM crawl_meta WHERE key = ?", [key]).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.con.execute("INSERT OR REPLACE INTO crawl_meta VALUES (?, ?)", [key, value])

    # -- async producer API -----------------------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="db-writer")

    async def put(self, table: str, rows: list[dict]) -> None:
        if rows:
            await self._queue.put((table, rows))

    async def drain(self) -> None:
        """Wait until everything queued so far is committed."""
        await self._queue.join()

    async def close(self) -> None:
        await self._queue.put(None)
        if self._task:
            await self._task
        self.con.execute("CHECKPOINT")
        self.con.close()

    # -- flush loop -------------------------------------------------------------------------------------------

    async def _run(self) -> None:
        pending: list[tuple[str, list[dict]]] = []
        pending_rows = 0
        last_flush = time.monotonic()
        last_checkpoint = last_flush
        stop = False
        while not stop:
            timeout = max(0.05, FLUSH_SECONDS - (time.monotonic() - last_flush))
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout)
            except TimeoutError:
                item = "timeout"
            if item is None:
                stop = True
            elif item != "timeout":
                pending.append(item)
                pending_rows += len(item[1])
            if pending and (stop or pending_rows >= FLUSH_ROWS or time.monotonic() - last_flush >= FLUSH_SECONDS):
                batch, pending, n = pending, [], pending_rows
                pending_rows = 0
                await asyncio.to_thread(self._flush, batch)
                for _ in batch:
                    self._queue.task_done()
                last_flush = time.monotonic()
                if last_flush - last_checkpoint > CHECKPOINT_SECONDS:
                    await asyncio.to_thread(self.con.execute, "CHECKPOINT")
                    last_checkpoint = last_flush
                log.debug("flushed %d rows", n)
            if item is None or item == "timeout":
                continue
        # mark the sentinel done too
        self._queue.task_done()

    def _flush(self, batch: list[tuple[str, list[dict]]]) -> None:
        grouped: dict[str, list[dict]] = defaultdict(list)
        order: list[str] = []
        for table, rows in batch:
            if table not in grouped:
                order.append(table)
            grouped[table].extend(rows)
        self.con.execute("BEGIN")
        try:
            for table in order:
                rows = grouped[table]
                arrow = pa.Table.from_pylist(rows, schema=SCHEMAS[table])
                self.con.register("_batch", arrow)
                self.con.execute(
                    f"INSERT OR REPLACE INTO {table} SELECT * FROM _batch"
                    if table == "crawl_checkpoint"
                    else f"INSERT INTO {table} SELECT * FROM _batch"
                )
                self.con.unregister("_batch")
                self.rows_written[table] += len(rows)
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
