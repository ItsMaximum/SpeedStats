"""Runtime configuration, read from environment variables / a local .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]  # comma-separated in the environment


def _csv(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return list(value) if value else []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Path("./data")
    flag_cache_dir: Path | None = None  # default: DATA_DIR/flags (the API container mounts DATA_DIR read-only)

    # scraper
    src_proxies: CsvList = []
    src_per_proxy_concurrency: int = 2
    src_per_proxy_rpm: float = 100.0  # speedrun.com allows ~100 requests/minute per IP
    src_max_attempts: int = 20
    src_timeout: float = 60.0
    games_in_flight: int = 48
    excluded_games: CsvList = ["w6jrzxdj"]  # Speed Builders
    excluded_categories: CsvList = ["n2y350ed", "5dw43j0k"]  # Subway Surfers No Coins, MC Legacy Console Time Attack
    excluded_players: CsvList = []

    # publish
    min_leaderboards: int = 600_000
    api_health_url: str = "http://localhost:8000/health"  # "" to skip waiting for the API
    cloudflare_zone_id: str = ""
    cloudflare_api_token: str = ""
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "speedstats"
    public_url: str = "https://speedstats.app"

    @field_validator("src_proxies", "excluded_games", "excluded_categories", "excluded_players", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> list[str]:
        return _csv(value)

    @property
    def flag_cache(self) -> Path:
        return self.flag_cache_dir or self.data_dir / "flags"

    @property
    def use_proxy(self) -> bool:
        return bool(self.src_proxies)


settings = Settings()
