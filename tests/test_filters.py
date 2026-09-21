from urllib.parse import parse_qsl

import pytest

from speedstats.filters import BoxTerms, parse_query, to_params

# Shared with web/src/query.test.ts - keep the two tables in sync.
CASES = [
    # links from the original site: long keys, ", " separated
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
    # the form the site writes: short keys, comma separated
    ("g=redball,redball2&r=pr", {"games": BoxTerms(("redball", "redball2")), "request_type": "pr"}),
    ("s=a,+b+,c", {"series": BoxTerms(("a", "b", "c"))}),  # whitespace around a term is ignored
    ("g=!a,b,!c", {"games": BoxTerms(("b",), ("a", "c"))}),
    ("g=a,,b,", {"games": BoxTerms(("a", "b"))}),  # empty pieces are dropped
    ("g=a&games=b", {"games": BoxTerms(("a", "b"))}),  # repeats merge, whichever spelling
    ("l=us,!ca", {"locations": BoxTerms(("us",), ("ca",))}),
    ("u=Maximum&p=PC", {"players": BoxTerms(("Maximum",)), "platforms": BoxTerms(("PC",))}),
    ("r=runs", {"request_type": "runs"}),
    ("m=50", {"limit": 50}),
    ("countries=us", {"locations": BoxTerms()}),  # not a parameter (the original site had no such box)
    # defaults & oddities
    ("", {"request_type": "pr", "limit": 1000}),
    ("g=!", {"games": BoxTerms()}),
    ("g=+Red+Ball+", {"games": BoxTerms(("Red Ball",))}),
    ("g=Red+Ball,red+ball", {"games": BoxTerms(("Red Ball",))}),
    ("limit=99999", {"limit": 5000}),
    ("limit=10", {"limit": 10}),
]


@pytest.mark.parametrize(("query", "expected"), CASES)
def test_parse_query(query, expected):
    spec = parse_query(parse_qsl(query, keep_blank_values=True))
    for attr, value in expected.items():
        assert getattr(spec, attr) == value, attr


def test_unknown_request_type_warns_and_defaults():
    spec = parse_query([("r", "bogus")])
    assert spec.request_type == "pr"
    assert spec.warnings and "bogus" in spec.warnings[0]


def test_invalid_limit_warns():
    spec = parse_query([("m", "abc")])
    assert spec.limit == 1000
    assert spec.warnings


def test_has_scope_terms():
    assert not parse_query([("g", "!X")]).has_scope_terms
    assert parse_query([("p", "PC")]).has_scope_terms


def test_terms_keep_the_order_given():
    spec = parse_query(parse_qsl("g=!A,B&g=!C,b"))
    assert spec.games.signed == ("!A", "B", "!C")  # duplicates dropped, includes and excludes interleaved as typed
    assert to_params(spec)[0] == ("g", "!A,B,!C")
    assert BoxTerms(("A",), ("B",)).signed == ("A", "!B")  # built by hand: includes first, then excludes


def test_to_params_round_trip():
    spec = parse_query(parse_qsl("series=Red+Ball&games=!Red+Ball+5&locations=us&request-type=runs&limit=50"))
    params = to_params(spec)
    assert params == [("s", "Red Ball"), ("g", "!Red Ball 5"), ("l", "us"), ("r", "runs"), ("m", "50")]
    assert parse_query(params) == spec
    assert to_params(parse_query([])) == [("r", "pr")]  # empty boxes are left out, the default limit too
