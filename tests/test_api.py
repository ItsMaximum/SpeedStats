import pytest
from fastapi.testclient import TestClient

from api import main


@pytest.fixture(scope="module")
def client(fixture_data_dir):
    main.holder.data_dir = fixture_data_dir
    with TestClient(main.app) as c:
        yield c


def query(client, qs):
    r = client.get(f"/api/query?{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def test_healthz_is_liveness_only(client):
    assert client.get("/healthz").json() == {"ok": True, "data": True}


def test_health_and_meta(client):
    assert client.get("/health").json()["data_version"] == "test"
    meta = client.get("/api/meta").json()
    assert meta["row_count"] == 8994 and meta["stale"] is True  # fixture scrape is from 2025


def test_legacy_link_player_rankings(client):
    out = query(client, "series=&games=Red+Ball&platforms=&players=&request-type=pr")
    assert out["columns"] == ["Rank", "Player", "Points"]
    assert out["rows"][0][0] == 1 and out["rows"][0][2] > 0
    assert out["title"] == "Player Rankings - Red Ball"
    assert out["truncated"] is False or len(out["rows"]) == 1000


def test_all_games_ranking_keeps_global_rank_for_player_lookup(client):
    everyone = query(client, "request-type=pr&limit=20")["rows"]
    two = query(client, "players=Maximum%2C+Niftski&request-type=pr")["rows"]
    ranks = {row[1]: row[0] for row in everyone}
    for rank, player, _points in two:
        assert ranks[player] == rank


def test_exclusion_across_boxes(client):
    all_rb = query(client, "series=Red+Ball&request-type=games&v=2")["rows"]
    minus = query(client, "series=Red+Ball&games=-Red+Ball+2&request-type=games&v=2")["rows"]
    assert any(r[1] == "Red Ball 2" for r in all_rb)
    assert not any(r[1] == "Red Ball 2" for r in minus)
    assert len(minus) == len(all_rb) - 1


def test_only_exclusions_means_everything_else(client):
    out = query(client, "games=-Red+Ball&request-type=games&v=2")
    names = [r[1] for r in out["rows"]]
    assert "Red Ball" not in names and len(names) > 5


def test_unmatched_include_term_gives_empty_result_and_warning(client):
    out = query(client, "games=Nope+Game&request-type=pr")
    assert out["rows"] == [] and "Nope Game" in out["warnings"][0]


def test_single_player_drops_player_column(client):
    one = query(client, "players=Maximum&request-type=runs&limit=5")
    assert one["columns"] == ["Rank", "Leaderboard", "Place", "Value"]
    two = query(client, "players=Maximum&players=Niftski&request-type=runs&limit=5&v=2")
    assert two["columns"] == ["Rank", "Player", "Leaderboard", "Place", "Value"]


@pytest.mark.parametrize(
    ("request_type", "second_column"),
    [
        ("records", "Leaderboard"),
        ("leaderboards", "Leaderboard"),
        ("games", "Game"),
        ("series", "Series"),
        ("dates", "Date"),
    ],
)
def test_other_request_types(client, request_type, second_column):
    out = query(client, f"request-type={request_type}&limit=5")
    assert out["columns"][1] == second_column
    assert out["rows"][0][0] == 1
    if request_type != "series":  # the fixture only has one series
        assert len(out["rows"]) == 5 and out["truncated"] is True


def test_records_one_row_per_leaderboard(client):
    out = query(client, "request-type=records&limit=5000")
    boards = [r[1] for r in out["rows"]]
    assert len(boards) == len(set(boards))
    assert [r[0] for r in out["rows"]] == list(range(1, len(boards) + 1))


def test_platform_scope_and_limit(client):
    out = query(client, "platforms=Web&request-type=runs&limit=7&v=2")
    assert len(out["rows"]) == 7 and out["truncated"]


def test_unknown_request_type_warns(client):
    out = query(client, "request-type=bogus&limit=1")
    assert out["request_type"] == "pr" and out["warnings"]


def test_csv_export(client):
    r = client.get("/api/query?request-type=runs&limit=2&format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "Rank,Player,Leaderboard,Place,Value" and len(lines) == 3


def test_cache_headers(client):
    r = client.get("/api/query?games=Red+Ball&request-type=pr")
    assert r.headers["cache-control"].startswith("public") and r.headers["etag"].startswith('"test-')


def test_suggest(client):
    items = client.get("/api/suggest?box=games&q=red").json()["items"]
    assert items[0]["name"] == "Red Ball"
    assert client.get("/api/suggest?box=players&q=zzzzzzzz").json()["items"] == []
    assert client.get("/api/suggest?box=nope&q=x").status_code == 422


def test_shell_serves_something(client):
    assert client.get("/index.php?games=Red+Ball").status_code == 200
    assert client.get("/api/nope").status_code == 404
