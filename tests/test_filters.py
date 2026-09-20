from urllib.parse import parse_qsl

import pytest

from speedstats.filters import BoxTerms, parse_query, to_params

# Shared with web/src/query.test.ts - keep the two tables in sync.
CASES = [
    # legacy links: ", " separated, no v param
    (
        "series=&games=Red+Ball&platforms=&players=&request-type=pr",
        {"games": BoxTerms(("Red Ball",)), "request_type": "pr"},
    ),
    (
        "games=Red+Ball%2C+Red+Ball+2&request-type=runs",
        {"games": BoxTerms(("Red Ball", "Red Ball 2")), "request_type": "runs"},
    ),
    (
        "players=Maximum%2C+!Someone&request-type=records",
        {"players": BoxTerms(("Maximum",), ("Someone",)), "request_type": "records"},
    ),
    # legacy: a comma without a following space is NOT a separator (matches PHP explode(", "))
    ("games=A,B", {"games": BoxTerms(("A,B",))}),
    # v=2: never split
    ("games=Sonic%2C+Redux&v=2", {"games": BoxTerms(("Sonic, Redux",))}),
    (
        "series=Red+Ball&games=!Red+Ball+5&v=2&request-type=pr",
        {"series": BoxTerms(("Red Ball",)), "games": BoxTerms((), ("Red Ball 5",))},
    ),
    ("games=A&games=B&games=!C&v=2", {"games": BoxTerms(("A", "B"), ("C",))}),
    # locations box, and its former name as an alias
    ("locations=us&locations=!ca&v=2", {"locations": BoxTerms(("us",), ("ca",))}),
    ("countries=England&locations=!us&v=2", {"locations": BoxTerms(("England",), ("us",))}),
    # defaults & oddities
    ("", {"request_type": "pr", "limit": 1000}),
    ("games=!&v=2", {"games": BoxTerms()}),
    ("games=+Red+Ball+&v=2", {"games": BoxTerms(("Red Ball",))}),
    ("games=Red+Ball&games=red+ball&v=2", {"games": BoxTerms(("Red Ball",))}),
    ("limit=99999", {"limit": 5000}),
    ("limit=10", {"limit": 10}),
]


@pytest.mark.parametrize(("query", "expected"), CASES)
def test_parse_query(query, expected):
    spec = parse_query(parse_qsl(query, keep_blank_values=True))
    for attr, value in expected.items():
        assert getattr(spec, attr) == value, attr


def test_unknown_request_type_warns_and_defaults():
    spec = parse_query([("request-type", "bogus")])
    assert spec.request_type == "pr"
    assert spec.warnings and "bogus" in spec.warnings[0]


def test_invalid_limit_warns():
    spec = parse_query([("limit", "abc")])
    assert spec.limit == 1000
    assert spec.warnings


def test_has_scope_terms():
    assert not parse_query([("games", "!X"), ("v", "2")]).has_scope_terms
    assert parse_query([("platforms", "PC")]).has_scope_terms


def test_terms_keep_the_order_given():
    spec = parse_query(parse_qsl("games=!A&games=B&games=!C&games=b&v=2"))
    assert spec.games.signed == ("!A", "B", "!C")  # duplicates dropped, includes and excludes interleaved as typed
    assert to_params(spec)[:3] == [("games", "!A"), ("games", "B"), ("games", "!C")]
    assert BoxTerms(("A",), ("B",)).signed == ("A", "!B")  # built by hand: includes first, then excludes


def test_to_params_round_trip():
    spec = parse_query(parse_qsl("series=Red+Ball&games=!Red+Ball+5&locations=us&request-type=runs&limit=50&v=2"))
    params = to_params(spec)
    assert params == [
        ("series", "Red Ball"),
        ("games", "!Red Ball 5"),
        ("locations", "us"),
        ("request-type", "runs"),
        ("limit", "50"),
        ("v", "2"),
    ]
    assert parse_query(params) == spec
