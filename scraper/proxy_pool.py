"""Pool of cors-anywhere proxies (https://<app>.herokuapp.com/<url>), each with its own speedrun.com budget.

speedrun.com's v2 API allows **500 requests per 20-minute
window per IP**, shared by every endpoint. The window starts at the first accepted request after the previous
window expired (not on clock boundaries); 429s do not consume budget and do not extend the window; it is a
count, not a rate, so concurrency does not matter. The pool therefore gives each proxy a window budget and,
once it is spent, parks the proxy until its window ends. A 429 (e.g. a proxy sharing an IP with something
else) is handled the same way: wait for the window to end, not an exponential backoff.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("speedstats.proxies")

WINDOW_MARGIN = 5.0  # seconds added after a window's end before using the proxy again
UNKNOWN_WINDOW_BACKOFF = 60.0  # a 429 before the window start is known (should not happen)


@dataclass
class ProxyState:
    app: str  # Heroku app name, or "" for direct
    limit: int  # concurrent requests
    window_requests: int = 500
    window_seconds: float = 1200.0
    label: str = "direct"  # what logs call this proxy; never the app name
    inflight: int = 0
    window_start: float | None = None  # monotonic time of the first request of the current window
    window_used: int = 0
    available_at: float = 0.0  # penalties (transient errors) and exhausted windows
    requests: int = 0
    rate_limited: int = 0
    failures: int = 0
    windows: int = 0

    @property
    def base_url(self) -> str:
        return f"https://{self.app}.herokuapp.com/" if self.app else ""

    def window_end(self) -> float | None:
        return None if self.window_start is None else self.window_start + self.window_seconds + WINDOW_MARGIN

    def refresh(self, now: float) -> None:
        """Start a new window once the current one has expired."""
        end = self.window_end()
        if end is not None and now >= end:
            self.window_start = None
            self.window_used = 0

    def ready(self, now: float) -> bool:
        self.refresh(now)
        return self.inflight < self.limit and self.available_at <= now and self.window_used < self.window_requests

    def next_ready_at(self, now: float) -> float | None:
        """When this proxy could next accept a request, ignoring concurrency."""
        self.refresh(now)
        candidates = [self.available_at]
        if self.window_used >= self.window_requests and (end := self.window_end()) is not None:
            candidates.append(end)
        return max(candidates)

    def take(self, now: float) -> None:
        if self.window_start is None:
            self.window_start = now
            self.windows += 1
        self.window_used += 1
        self.inflight += 1
        self.requests += 1


@dataclass
class ProxyPool:
    proxies: list[ProxyState]
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    @classmethod
    def build(
        cls,
        apps: list[str],
        per_proxy: int,
        window_requests: int = 500,
        window_seconds: float = 1200.0,
        direct_limit: int = 4,
    ) -> ProxyPool:
        if not apps:
            return cls([ProxyState("", direct_limit, window_requests, window_seconds)])
        return cls(
            [
                ProxyState(app, per_proxy, window_requests, window_seconds, f"proxy-{i}")
                for i, app in enumerate(apps, start=1)
            ]
        )

    def redact(self, text: str) -> str:
        """Replace proxy hostnames in `text` (e.g. from an httpx error) with their labels."""
        for p in self.proxies:
            if p.app:
                text = text.replace(f"{p.app}.herokuapp.com", p.label).replace(p.app, p.label)
        return text

    @property
    def capacity(self) -> int:
        return sum(p.limit for p in self.proxies)

    @property
    def budget_per_window(self) -> int:
        return sum(p.window_requests for p in self.proxies)

    async def acquire(self) -> ProxyState:
        """A proxy with budget left in its window, least loaded first; waits until one has."""
        async with self._cond:
            while True:
                now = time.monotonic()
                ready = [p for p in self.proxies if p.ready(now)]
                if ready:
                    chosen = min(ready, key=lambda p: (p.inflight, p.window_used))
                    chosen.take(now)
                    return chosen
                waits = [t - now for p in self.proxies if (t := p.next_ready_at(now)) is not None and t > now]
                timeout = max(0.05, min(waits)) if waits else None
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout)
                except TimeoutError:
                    pass

    async def release(self, proxy: ProxyState) -> None:
        async with self._cond:
            proxy.inflight -= 1
            self._cond.notify_all()

    async def success(self, proxy: ProxyState) -> None:
        pass

    async def rate_limited(self, proxy: ProxyState, retry_after: float | None = None) -> None:
        """The server says the budget is gone: park the proxy until its window ends (rejections cost nothing)."""
        now = time.monotonic()
        proxy.rate_limited += 1
        proxy.window_used = max(proxy.window_used, proxy.window_requests)
        if proxy.available_at > now:
            return  # already parked; a burst of 429s from requests in flight is one event
        end = proxy.window_end()
        delay = retry_after if retry_after else (end - now if end is not None else UNKNOWN_WINDOW_BACKOFF)
        proxy.available_at = now + max(delay, WINDOW_MARGIN)
        log.warning(
            "%s rate limited after %d requests this window; waiting %.0fs for the window to end",
            proxy.label,
            proxy.window_used,
            proxy.available_at - now,
        )

    async def penalize(self, proxy: ProxyState, seconds: float) -> None:
        proxy.failures += 1
        proxy.available_at = max(proxy.available_at, time.monotonic() + seconds)

    def stats(self) -> str:
        return ", ".join(f"{p.label}:{p.requests}r/{p.windows}w/{p.rate_limited}rl/{p.failures}f" for p in self.proxies)
