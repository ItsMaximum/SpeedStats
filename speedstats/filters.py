"""Query-string parsing shared by the API (and mirrored in web/src/query.ts).

Two URL formats are accepted:

* legacy (no ``v`` param): every occurrence of a box param is split on ", " exactly like the old PHP
  ``explode(", ", ...)`` did, so links shared before the rewrite keep working.
* v=2: one term per param occurrence (``games=A&games=B``); never split, so names containing ", " work.

A term starting with ``-`` excludes instead of includes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

BOXES = ("series", "games", "platforms", "players", "countries")
REQUEST_TYPES = ("pr", "runs", "records", "leaderboards", "games", "series", "dates")
DEFAULT_REQUEST_TYPE = "pr"
DEFAULT_LIMIT = 1000
MAX_LIMIT = 5000
LEGACY_SEPARATOR = ", "
FORMAT_VERSION = "2"


@dataclass(frozen=True)
class BoxTerms:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class FilterSpec:
    series: BoxTerms = BoxTerms()
    games: BoxTerms = BoxTerms()
    platforms: BoxTerms = BoxTerms()
    players: BoxTerms = BoxTerms()
    countries: BoxTerms = BoxTerms()
    request_type: str = DEFAULT_REQUEST_TYPE
    limit: int = DEFAULT_LIMIT
    warnings: tuple[str, ...] = field(default=())

    def box(self, name: str) -> BoxTerms:
        return getattr(self, name)

    @property
    def has_scope_terms(self) -> bool:
        """True when any series/games/platforms include-term was given (scope is not "all runs")."""
        return bool(self.series.include or self.games.include or self.platforms.include)


def split_terms(values: Iterable[str], new_format: bool) -> list[str]:
    terms: list[str] = []
    for value in values:
        parts = [value] if new_format else value.split(LEGACY_SEPARATOR)
        terms.extend(part.strip() for part in parts)
    return [term for term in terms if term]


def split_sign(terms: Iterable[str]) -> BoxTerms:
    include: list[str] = []
    exclude: list[str] = []
    for term in terms:
        if term.startswith("-"):
            stripped = term[1:].strip()
            if stripped:
                exclude.append(stripped)
        else:
            include.append(term)
    return BoxTerms(tuple(_dedupe(include)), tuple(_dedupe(exclude)))


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
        grouped.setdefault(key, []).append(value)

    new_format = FORMAT_VERSION in grouped.get("v", [])
    warnings: list[str] = []

    boxes = {name: split_sign(split_terms(grouped.get(name, []), new_format)) for name in BOXES}

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
    """Canonical v=2 query params for a spec (the form the UI emits)."""
    params: list[tuple[str, str]] = []
    for name in BOXES:
        box = spec.box(name)
        params.extend((name, term) for term in box.include)
        params.extend((name, f"-{term}") for term in box.exclude)
    params.append(("request-type", spec.request_type))
    if spec.limit != DEFAULT_LIMIT:
        params.append(("limit", str(spec.limit)))
    params.append(("v", FORMAT_VERSION))
    return params
