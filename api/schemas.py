from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MetaOut(BaseModel):
    data_version: str
    scraped_at: datetime
    published_at: datetime
    row_count: int
    leaderboard_count: int
    player_count: int
    game_count: int
    stale: bool


class HealthOut(BaseModel):
    ok: bool
    data_version: str | None = None
    scraped_at: datetime | None = None
    stale: bool | None = None


class QueryOut(BaseModel):
    request_type: str
    title: str
    columns: list[str]
    rows: list[list[str | int | float | None]]
    truncated: bool
    warnings: list[str]
    flags: dict[str, str]  # flag id (e.g. "gb/eng") -> area name, for the flags present in `rows`
    meta: MetaOut


class Suggestion(BaseModel):
    name: str
    slug: str | None = None


class SuggestOut(BaseModel):
    box: str
    q: str
    items: list[Suggestion]
