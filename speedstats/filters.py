"""Query-string parsing shared by the API (and mirrored in web/src/query.ts).

A query is ``?s=<series>&g=<games>&p=<platforms>&u=<players>&l=<locations>&r=<request type>&m=<limit>``, every
box a comma-separated list of terms (``g=redball,redball2``); whitespace around a term is ignored. The long
names the original site used (``series``, ``games``, ``platforms``, ``players``, ``request-type``, ``limit``;
``locations``) are accepted too, so its ``games=Red+Ball%2C+Red+Ball+2`` links keep working. A term starting
with ``!`` excludes instead of includes (no speedrun.com name starts with ``!``); terms cannot contain a comma.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

BOXES = ("series", "games", "platforms", "players", "locations")
REQUEST_TYPES = ("pr", "runs", "records", "leaderboards", "games", "series", "dates")
DEFAULT_REQUEST_TYPE = "pr"
DEFAULT_LIMIT = 1000
MAX_LIMIT = 5000
SEPARATOR = ","

# The one-letter query keys links are written with, and the long forms the original site used.
SHORT_KEY = {"series": "s", "games": "g", "platforms": "p", "players": "u", "locations": "l"}
REQUEST_TYPE_KEY = "r"
LIMIT_KEY = "m"
PARAM_ALIASES: dict[str, str] = {
    **{short: box for box, short in SHORT_KEY.items()},
    **{box: box for box in BOXES},
    REQUEST_TYPE_KEY: "request-type",
    "request-type": "request-type",
    LIMIT_KEY: "limit",
    "limit": "limit",
}


@dataclass(frozen=True)
class BoxTerms:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    # every term as given (with its "!"), in the order given: what the URL, the title and the chips keep.
    # Derived from include + exclude when a BoxTerms is built by hand.
    signed: tuple[str, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        if not self.signed and (self.include or self.exclude):
            object.__setattr__(self, "signed", self.include + tuple(f"!{t}" for t in self.exclude))


def split_term(term: str) -> tuple[str, str]:
    """("!" or "", the term without its sign)."""
    return ("!", term[1:].strip()) if term.startswith("!") else ("", term)


@dataclass(frozen=True)
class FilterSpec:
    series: BoxTerms = BoxTerms()
    games: BoxTerms = BoxTerms()
    platforms: BoxTerms = BoxTerms()
    players: BoxTerms = BoxTerms()
    locations: BoxTerms = BoxTerms()
    request_type: str = DEFAULT_REQUEST_TYPE
    limit: int = DEFAULT_LIMIT
    warnings: tuple[str, ...] = field(default=())

    def box(self, name: str) -> BoxTerms:
        return getattr(self, name)

    @property
    def has_scope_terms(self) -> bool:
        """True when any series/games/platforms include-term was given (scope is not "all runs")."""
        return bool(self.series.include or self.games.include or self.platforms.include)


def split_terms(values: Iterable[str]) -> list[str]:
    """Every value is a comma-separated list; the pieces are trimmed and empty ones dropped."""
    terms: list[str] = []
    for value in values:
        terms.extend(part.strip() for part in value.split(SEPARATOR))
    return [term for term in terms if term]


def split_sign(terms: Iterable[str]) -> BoxTerms:
    include: list[str] = []
    exclude: list[str] = []
    signed: list[str] = []
    for term in terms:
        sign, bare = split_term(term)
        if not bare:
            continue  # a lone "!"
        (exclude if sign else include).append(bare)
        signed.append(sign + bare)
    return BoxTerms(tuple(_dedupe(include)), tuple(_dedupe(exclude)), tuple(_dedupe(signed)))


def _dedupe(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            out.append(term)
    return out


def parse_query(params: Iterable[tuple[str, str]]) -> FilterSpec:
    """Parse ``(key, value)`` pairs (in order, repeats allowed) into a FilterSpec."""
    grouped: dict[str, list[str]] = {}
    for key, value in params:
        if canonical := PARAM_ALIASES.get(key):
            grouped.setdefault(canonical, []).append(value)

    warnings: list[str] = []
    boxes = {name: split_sign(split_terms(grouped.get(name, []))) for name in BOXES}

    request_type = (grouped.get("request-type") or [DEFAULT_REQUEST_TYPE])[-1].strip() or DEFAULT_REQUEST_TYPE
    if request_type not in REQUEST_TYPES:
        warnings.append(f"Unknown request type '{request_type}'; showing Player Rankings instead.")
        request_type = DEFAULT_REQUEST_TYPE

    limit = DEFAULT_LIMIT
    if raw_limit := (grouped.get("limit") or [""])[-1].strip():
        try:
            limit = max(1, min(MAX_LIMIT, int(raw_limit)))
        except ValueError:
            warnings.append(f"Ignoring invalid limit '{raw_limit}'.")

    return FilterSpec(**boxes, request_type=request_type, limit=limit, warnings=tuple(warnings))


def to_params(spec: FilterSpec) -> list[tuple[str, str]]:
    """Canonical query params for a spec (the form the UI emits): short keys, comma-joined terms."""
    params: list[tuple[str, str]] = []
    for name in BOXES:
        if signed := spec.box(name).signed:
            params.append((SHORT_KEY[name], SEPARATOR.join(signed)))
    params.append((REQUEST_TYPE_KEY, spec.request_type))
    if spec.limit != DEFAULT_LIMIT:
        params.append((LIMIT_KEY, str(spec.limit)))
    return params
