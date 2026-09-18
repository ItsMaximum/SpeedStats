"""Layout of DATA_DIR and the atomic CURRENT pointer that names the live database file."""

from __future__ import annotations

import os
from pathlib import Path

CURRENT = "CURRENT"
WORK = "work"


def work_dir(data_dir: Path) -> Path:
    path = data_dir / WORK
    path.mkdir(parents=True, exist_ok=True)
    return path


def crawl_path(data_dir: Path, version: str) -> Path:
    return work_dir(data_dir) / f"crawl-{version}.duckdb"


def published_path(data_dir: Path, version: str) -> Path:
    return data_dir / f"speedstats-{version}.duckdb"


def read_current(data_dir: Path) -> Path | None:
    """The live database file, or None when nothing has been published yet."""
    pointer = data_dir / CURRENT
    try:
        name = pointer.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not name:
        return None
    path = data_dir / name
    return path if path.exists() else None


def write_current(data_dir: Path, published: Path) -> None:
    """Atomically point CURRENT at `published` (write temp file, then os.replace)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    pointer = data_dir / CURRENT
    tmp = data_dir / f"{CURRENT}.tmp"
    tmp.write_text(published.name + "\n", encoding="utf-8")
    os.replace(tmp, pointer)


def list_published(data_dir: Path) -> list[Path]:
    """Published files, oldest first (versions are lexically sortable timestamps)."""
    return sorted(data_dir.glob("speedstats-*.duckdb"))
