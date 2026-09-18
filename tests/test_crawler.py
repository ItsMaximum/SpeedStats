import asyncio
import json

import httpx
import pytest

from scraper.proxy_pool import ProxyPool
from scraper.src_client import API_URI, CrawlError, NotFound, SrcClient, encode_params
from scraper.writer import DbWriter


def make_client(handler, apps, per_proxy=1, max_attempts=6):
    pool = ProxyPool.build(apps, per_proxy)
    client = SrcClient(pool, max_attempts=max_attempts, timeout=5, transient_delay=0.01)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="")
    return client, pool


def test_encode_params_matches_site_encoding():
    assert encode_params({"gameId": "j1llwz1g"}) == "eyJnYW1lSWQiOiJqMWxsd3oxZyJ9"
    assert "=" not in encode_params({"page": 2, "params": {"a": 1}})


async def test_rate_limited_proxy_is_skipped_and_request_succeeds_elsewhere():
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        hits.append(host)
        if host.startswith("bad"):
            return httpx.Response(429, headers={"Retry-After": "600"})
        return httpx.Response(200, json={"ok": True, "via": host})

    client, pool = make_client(handler, ["bad", "good"])
    out = await client.request("GetStaticData")
    assert out["ok"]
    assert "good.herokuapp.com" in hits[-1]
    bad = next(p for p in pool.proxies if p.app == "bad")
    assert bad.strikes == 1 and bad.available_at > 0
    # the rate-limited proxy is not used again while backing off
    for _ in range(3):
        await client.request("GetStaticData")
    assert all(h.startswith("good") for h in hits[hits.index("good.herokuapp.com") :])
    await client.aclose()


async def test_transient_errors_retry_then_give_up():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client, _ = make_client(handler, [], max_attempts=3)
    with pytest.raises(CrawlError):
        await client.request("GetGameData", {"gameId": "x"})
    assert calls == 3
    await client.aclose()


async def test_404_is_not_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    client, _ = make_client(handler, [])
    with pytest.raises(NotFound):
        await client.get_game_data("nope")
    assert calls == 1
    await client.aclose()


async def test_leaderboard_endpoints_are_normalised():
    def handler(request):
        params = json.loads(
            httpx.URL(str(request.url)).params["_r"] + "=="
            and __import__("base64").urlsafe_b64decode(httpx.URL(str(request.url)).params["_r"] + "==")
        )
        assert params["params"]["obsolete"] == 1 and params["params"]["verified"] == 1
        if request.url.path.endswith("GetGameLeaderboard"):
            body = {"leaderboard": {"players": [{"id": "p"}], "runs": [{"id": "r"}], "pagination": {"pages": 3}}}
        else:
            body = {"playerList": [{"id": "p"}], "runList": [{"id": "r"}], "pagination": {"pages": 2}}
        return httpx.Response(200, json=body)

    client, _ = make_client(handler, [])
    one = await client.get_leaderboard("g", "c", 1, 1)
    two = await client.get_leaderboard("g", "c", 1, 2)
    assert one.pages == 3 and two.pages == 2 and one.runs == two.runs == [{"id": "r"}]
    await client.aclose()


async def test_direct_mode_url_and_proxy_url():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    client, _ = make_client(handler, [])
    await client.request("GetStaticData")
    assert seen[-1].startswith(API_URI + "GetStaticData?_r=")
    client, _ = make_client(handler, ["proxy-a"])
    await client.request("GetStaticData")
    assert seen[-1].startswith("https://proxy-a.herokuapp.com/" + API_URI + "GetStaticData")
    await client.aclose()


async def test_writer_commits_in_fifo_order_and_resumes_ord(tmp_path):
    path = tmp_path / "crawl.duckdb"
    writer = DbWriter(path)
    writer.start()
    ords = [writer.next_ord() for _ in range(3)]
    await writer.put(
        "raw_runs",
        [
            {
                "ord": o,
                "run_id": f"r{o}",
                "game_id": "g",
                "category_id": "c",
                "level_id": None,
                "value_ids": [],
                "player_ids": ["p"],
                "platform_id": None,
                "time": 1.0,
                "time_with_loads": None,
                "igt": None,
                "date": 0,
                "date_submitted": None,
                "lb_type": 1,
                "page": 1,
            }
            for o in ords
        ],
    )
    await writer.put(
        "crawl_checkpoint",
        [
            {
                "game_id": "g",
                "status": "done",
                "categories": 1,
                "pages": 1,
                "runs": 3,
                "error": None,
                "finished_at": __import__("datetime").datetime(2026, 1, 1),
            }
        ],
    )
    await writer.drain()
    assert writer.con.execute("SELECT COUNT(*) FROM raw_runs").fetchone()[0] == 3
    assert writer.con.execute("SELECT status FROM crawl_checkpoint").fetchone()[0] == "done"
    # replacing a checkpoint keeps one row per game
    await writer.put(
        "crawl_checkpoint",
        [
            {
                "game_id": "g",
                "status": "error",
                "categories": 0,
                "pages": 0,
                "runs": 0,
                "error": "boom",
                "finished_at": __import__("datetime").datetime(2026, 1, 2),
            }
        ],
    )
    await writer.drain()
    assert writer.con.execute("SELECT status FROM crawl_checkpoint").fetchall() == [("error",)]
    writer.set_meta("stage", "static")
    await writer.close()

    reopened = DbWriter(path)
    assert reopened.next_ord() == 3 and reopened.get_meta("stage") == "static"
    reopened.con.close()


async def test_pool_waits_for_capacity():
    pool = ProxyPool.build([], per_proxy=1, direct_limit=1)
    p = await pool.acquire()
    waiter = asyncio.create_task(pool.acquire())
    await asyncio.sleep(0.05)
    assert not waiter.done()
    await pool.release(p)
    assert await asyncio.wait_for(waiter, 1) is p
