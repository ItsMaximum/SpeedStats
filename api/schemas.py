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


class PlayerStyle(BaseModel):
    """How speedrun.com shows the player: leaderboard flag and dark-mode name colour(s)."""

    flag: str | None = None
    flag_name: str | None = None
    color1: str | None = None
    color2: str | None = None


class QueryOut(BaseModel):
    request_type: str
    title: str
    columns: list[str]
    rows: list[list[str | int | float | None]]
    truncated: bool
    warnings: list[str]
    players: dict[str, PlayerStyle]  # by player name, for the players present in `rows`
    meta: MetaOut


class Suggestion(BaseModel):
    name: str
    slug: str | None = None


class SuggestOut(BaseModel):
    box: str
    q: str
    items: list[Suggestion]
