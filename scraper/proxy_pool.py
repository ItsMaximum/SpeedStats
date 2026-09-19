"""Round-robin pool of cors-anywhere proxies (https://<app>.herokuapp.com/<url>) with per-proxy concurrency
and backoff. speedrun.com rate-limits per IP, so a 429 takes one proxy out of rotation for a while instead of
slowing the whole crawl. With no proxies configured, a single direct "proxy" is used."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

log = logging.getLogger("speedstats.proxies")

RATE_LIMIT_BASE = 60.0
RATE_LIMIT_CAP = 1800.0


@dataclass
class ProxyState:
    app: str  # Heroku app name, or "" for direct
    limit: int
    min_interval: float = 0.0  # seconds between request starts (per-IP rate cap)
    label: str = "direct"  # what logs call this proxy; never the app name
    inflight: int = 0
    available_at: float = 0.0
    next_at: float = 0.0
    strikes: int = 0
    requests: int = 0
    rate_limited: int = 0
    failures: int = 0

    @property
    def base_url(self) -> str:
        return f"https://{self.app}.herokuapp.com/" if self.app else ""

    def ready(self, now: float) -> bool:
        return self.inflight < self.limit and self.available_at <= now and self.next_at <= now


@dataclass
class ProxyPool:
    proxies: list[ProxyState]
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    @classmethod
    def build(cls, apps: list[str], per_proxy: int, rpm: float = 100.0, direct_limit: int = 4) -> ProxyPool:
        """`rpm` caps request starts per proxy (speedrun.com limits per IP, ~100/min)."""
        interval = 60.0 / rpm if rpm > 0 else 0.0
        if not apps:
            return cls([ProxyState("", direct_limit, interval)])
        return cls([ProxyState(app, per_proxy, interval, f"proxy-{i}") for i, app in enumerate(apps, start=1)])

    def redact(self, text: str) -> str:
        """Replace proxy hostnames in `text` (e.g. from an httpx error) with their labels."""
        for p in self.proxies:
            if p.app:
                text = text.replace(f"{p.app}.herokuapp.com", p.label).replace(p.app, p.label)
        return text

    @property
    def capacity(self) -> int:
        return sum(p.limit for p in self.proxies)

    async def acquire(self) -> ProxyState:
        """The least-loaded proxy that is not backing off; waits until one frees up."""
        async with self._cond:
            while True:
                now = time.monotonic()
                ready = [p for p in self.proxies if p.ready(now)]
                if ready:
                    chosen = min(ready, key=lambda p: (p.inflight, p.requests))
                    chosen.inflight += 1
                    chosen.requests += 1
                    chosen.next_at = now + chosen.min_interval
                    return chosen
                waits = [
                    max(p.available_at, p.next_at) - now
                    for p in self.proxies
                    if max(p.available_at, p.next_at) > now and p.inflight < p.limit
                ]
                timeout = min(waits) if waits else None
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout)
                except TimeoutError:
                    pass

    async def release(self, proxy: ProxyState) -> None:
        async with self._cond:
            proxy.inflight -= 1
            self._cond.notify_all()

    async def success(self, proxy: ProxyState) -> None:
        proxy.strikes = 0

    async def rate_limited(self, proxy: ProxyState, retry_after: float | None = None) -> None:
        """Take the proxy out of rotation, exponentially longer for consecutive 429s."""
        now = time.monotonic()
        proxy.rate_limited += 1
        if proxy.available_at > now:
            return  # a burst of 429s from requests already in flight is one strike, not several
        delay = retry_after if retry_after else min(RATE_LIMIT_CAP, RATE_LIMIT_BASE * 2**proxy.strikes)
        delay *= random.uniform(1.0, 1.25)
        proxy.strikes += 1
        proxy.available_at = now + delay
        log.warning("%s rate limited; backing off %.0fs (strike %d)", proxy.label, delay, proxy.strikes)

    async def penalize(self, proxy: ProxyState, seconds: float) -> None:
        proxy.failures += 1
        proxy.available_at = max(proxy.available_at, time.monotonic() + seconds)

    def stats(self) -> str:
        return ", ".join(f"{p.label}:{p.requests}r/{p.rate_limited}rl/{p.failures}f" for p in self.proxies)
