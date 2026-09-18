"""Async client for the speedrun.com v2 API, through the proxy pool, with retries.

Params are sent as `_r=<urlsafe base64 JSON>` exactly like the site itself does (and the old speedruncompy fork).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from dataclasses import dataclass
from typing import Any

import httpx

from scraper.proxy_pool import ProxyPool

log = logging.getLogger("speedstats.src")

API_URI = "https://www.speedrun.com/api/v2/"
API_V1_URI = "https://www.speedrun.com/api/v1/"
HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en",
    "User-Agent": "SpeedStats/4 (+https://speedstats.app)",
    "X-Requested-With": "SpeedStats",  # cors-anywhere requires an origin-ish header
}
TRANSIENT_STATUSES = {408, 500, 502, 503, 504, 520, 521, 522, 523, 524}


class CrawlError(Exception):
    pass


class NotFound(CrawlError):
    pass


class ClientError(CrawlError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


@dataclass
class LeaderboardPage:
    players: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    pages: int


def encode_params(params: dict[str, Any]) -> str:
    raw = json.dumps(params, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class SrcClient:
    def __init__(self, pool: ProxyPool, max_attempts: int = 20, timeout: float = 60.0, transient_delay: float = 15.0):
        self.pool = pool
        self.max_attempts = max_attempts
        self.transient_delay = transient_delay
        self._client = httpx.AsyncClient(timeout=timeout, headers=HEADERS, http2=True, follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request(API_URI + endpoint, {"_r": encode_params(params or {})}, f"{endpoint} {params}")

    async def request_v1(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """The older v1 REST API; some data (series membership) is more complete there."""
        return await self._request(API_V1_URI + path, params or {}, f"v1 {path} {params}")

    async def _request(self, url: str, query: dict[str, Any], label: str) -> dict[str, Any]:
        last = "no attempts"
        for _attempt in range(self.max_attempts):
            proxy = await self.pool.acquire()
            try:
                resp = await self._client.get(f"{proxy.base_url}{url}", params=query)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = f"{type(e).__name__}: {e}"
                await self.pool.penalize(proxy, 2 * self.transient_delay)
                continue
            finally:
                await self.pool.release(proxy)

            status = resp.status_code
            if status == 429:
                await self.pool.rate_limited(proxy, _retry_after(resp))
                last = "HTTP 429"
                continue
            if status in TRANSIENT_STATUSES:
                last = f"HTTP {status}"
                await self.pool.penalize(proxy, self.transient_delay)
                await asyncio.sleep(random.uniform(0.05, 0.2) * self.transient_delay)
                continue
            if status == 404:
                raise NotFound(label)
            if 400 <= status < 500:
                raise ClientError(status, resp.text)
            try:
                data = resp.json()
            except ValueError:
                last = "invalid JSON"  # proxies occasionally return an HTML error page with 200
                await self.pool.penalize(proxy, self.transient_delay)
                continue
            await self.pool.success(proxy)
            return data
        raise CrawlError(f"{label}: gave up after {self.max_attempts} attempts ({last})")

    # -- endpoints ---------------------------------------------------------------------------------------------

    async def get_static_data(self) -> dict[str, Any]:
        return await self.request("GetStaticData")

    async def get_series_list(self, page: int) -> dict[str, Any]:
        return await self.request("GetSeriesList", {"page": page})

    async def get_game_list(self, page: int, series_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page}
        if series_id:
            params["seriesId"] = series_id
        return await self.request("GetGameList", params)

    async def get_series_games_v1(self, series_id: str, offset: int = 0, size: int = 200) -> dict[str, Any]:
        """v1 series game list; v2's GetGameList(seriesId) omits most games of some series (e.g. Harry Potter)."""
        return await self.request_v1(f"series/{series_id}/games", {"max": size, "offset": offset})

    async def get_game_data(self, game_id: str) -> dict[str, Any]:
        return await self.request("GetGameData", {"gameId": game_id})

    async def get_leaderboard(self, game_id: str, category_id: str, page: int, lb_type: int) -> LeaderboardPage:
        """Both leaderboard endpoints, normalised. Always obsolete runs included, verified only, no video filter."""
        params = {
            "params": {"gameId": game_id, "categoryId": category_id, "obsolete": 1, "video": 0, "verified": 1},
            "page": page,
        }
        if lb_type == 1:
            data = (await self.request("GetGameLeaderboard", params))["leaderboard"]
            return LeaderboardPage(data["players"], data["runs"], data["pagination"]["pages"])
        data = await self.request("GetGameLeaderboard2", params)
        return LeaderboardPage(data["playerList"], data["runList"], data["pagination"]["pages"])


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    try:
        return float(value) if value else None
    except ValueError:
        return None


async def dedupe_proxies_by_ip(apps: list[str], timeout: float = 30.0) -> list[str]:
    """Two Heroku apps can land on the same dyno IP and share speedrun.com's rate limit; keep one per IP."""
    if not apps:
        return []
    async with httpx.AsyncClient(timeout=timeout, headers=HEADERS) as client:

        async def ip_of(app: str) -> tuple[str, str | None]:
            try:
                r = await client.get(f"https://{app}.herokuapp.com/ip4only.me/api/")
                return app, r.text.split(",")[1].strip()
            except (httpx.HTTPError, IndexError) as e:
                log.warning("proxy %s unusable: %s", app, e)
                return app, None

        results = await asyncio.gather(*(ip_of(app) for app in apps))
    seen: dict[str, str] = {}
    for app, ip in results:
        if ip and ip not in seen:
            seen[ip] = app
        elif ip:
            log.info("proxy %s shares IP %s with %s; skipping", app, ip, seen[ip])
    usable = list(seen.values())
    log.info("%d of %d proxies usable", len(usable), len(apps))
    return usable
