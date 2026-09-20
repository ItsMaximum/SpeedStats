"""Turn the free-text terms of a FilterSpec into sets of ids, using the published lookup tables.

Every term matches a full name or a speedrun.com slug, case-insensitively. Locations match a continent (our own
table), or any speedrun.com area at any depth (country, state/region, city) by id, name or full name; a matched
area covers everything under it. Unmatched terms become warnings and are never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

from speedstats.continents import CODE_OF_CONTINENT, countries_in, match_continent
from speedstats.filters import BoxTerms, FilterSpec, split_term

BOX_LABELS = {
    "series": "series",
    "games": "game",
    "platforms": "platform",
    "players": "player",
    "locations": "location",
}

_BY_NAME_OR_SLUG = (
    "SELECT t.term, x.id, x.name, x.slug FROM terms t JOIN {table} x ON x.name_lower = t.term OR x.slug_lower = t.term"
)
_LOOKUP_SQL = {
    "games": _BY_NAME_OR_SLUG.format(table="games"),
    "series": _BY_NAME_OR_SLUG.format(table="series"),
    "platforms": _BY_NAME_OR_SLUG.format(table="platforms"),
    "players": _BY_NAME_OR_SLUG.format(table="players"),
    "locations": (
        "SELECT t.term, x.id, coalesce(x.full_name, x.name), x.id FROM terms t JOIN areas x "
        "ON x.id_lower = t.term OR x.name_lower = t.term OR lower(x.full_name) = t.term"
    ),
}


@dataclass
class ResolvedFilter:
    inc_games: list[str] = field(default_factory=list)
    exc_games: list[str] = field(default_factory=list)
    inc_platforms: list[str] = field(default_factory=list)
    exc_platforms: list[str] = field(default_factory=list)
    inc_players: list[str] = field(default_factory=list)
    exc_players: list[str] = field(default_factory=list)
    inc_areas: list[str] = field(default_factory=list)  # every area id (any depth) selected by the locations box
    exc_areas: list[str] = field(default_factory=list)
    all_scope: bool = True  # no series/games/platforms include-term given
    all_players: bool = True  # no players include-term given
    all_locations: bool = True  # no locations include-term given
    single_player: bool = False  # exactly one player include-term: tables drop the Player column (as before)
    labels: dict[str, list[str]] = field(default_factory=dict)  # canonical display names per box, for titles
    # box -> term as given -> {"name": display name, "slug": abbreviation or None when the term matched more than
    # one thing}; the client shows the name on the chip and rewrites the URL to the slug (api.schemas.TermInfo)
    terms: dict[str, dict[str, dict[str, str | None]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def unfiltered_ranking(self) -> bool:
        """True when Player Rankings can be served straight from the precomputed player_ranks table."""
        return self.all_scope and not self.exc_games and not self.exc_platforms

    def params(self, limit: int) -> dict[str, object]:
        return {
            "inc_games": self.inc_games,
            "exc_games": self.exc_games,
            "inc_platforms": self.inc_platforms,
            "exc_platforms": self.exc_platforms,
            "inc_players": self.inc_players,
            "exc_players": self.exc_players,
            "inc_areas": self.inc_areas,
            "exc_areas": self.exc_areas,
            "all_scope": self.all_scope,
            "all_players": self.all_players,
            "all_locations": self.all_locations,
            "limit": limit,
        }


def _lookup(
    con: duckdb.DuckDBPyConnection, box: str, terms: tuple[str, ...]
) -> dict[str, list[tuple[str, str, str | None]]]:
    """term (casefolded) -> [(id, name, slug), ...]"""
    if not terms:
        return {}
    con.execute(
        "CREATE OR REPLACE TEMP TABLE terms AS SELECT DISTINCT unnest($terms::VARCHAR[]) AS term",
        {"terms": [t.casefold() for t in terms]},
    )
    found: dict[str, list[tuple[str, str, str | None]]] = {}
    for term, id_, name, slug in con.execute(_LOOKUP_SQL[box]).fetchall():
        found.setdefault(term, []).append((id_, name, slug))
    return found


def _remember(out: ResolvedFilter, box: str, term: str, matches: list[tuple[str, str, str | None]]) -> None:
    out.terms.setdefault(box, {})[term] = {"name": matches[0][1], "slug": matches[0][2] if len(matches) == 1 else None}


def _resolve_box(
    con: duckdb.DuckDBPyConnection, box: str, terms: BoxTerms, out: ResolvedFilter
) -> tuple[list[str], list[str]]:
    """Terms are walked in the order given, so the title lists them as the user wrote them."""
    hits = _lookup(con, box, terms.include + terms.exclude)
    label = BOX_LABELS[box]
    names: list[str] = []
    include: list[str] = []
    exclude: list[str] = []
    for signed in terms.signed:
        sign, term = split_term(signed)
        matches = hits.get(term.casefold(), [])
        if not matches:
            out.warnings.append(f"No {label} matched '{term}'.")
            continue
        (exclude if sign else include).extend(id_ for id_, _, _ in matches)
        names.append(sign + matches[0][1])
        _remember(out, box, term, matches)
    if names:
        out.labels[box] = names
    return include, exclude


def _resolve_locations(
    con: duckdb.DuckDBPyConnection, terms: BoxTerms, out: ResolvedFilter
) -> tuple[list[str], list[str]]:
    """Continent names or codes expand to their countries (a code beats a same-lettered country id); every
    matched area expands to itself plus all areas under it."""
    hits = _lookup(con, "locations", terms.include + terms.exclude)
    names: list[str] = []
    roots: dict[str, list[str]] = {"": [], "!": []}
    for signed in terms.signed:
        sign, term = split_term(signed)
        if continent := match_continent(term):
            roots[sign].extend(countries_in(continent))
            names.append(sign + continent)
            out.terms.setdefault("locations", {})[term] = {"name": continent, "slug": CODE_OF_CONTINENT[continent]}
            continue
        matches = hits.get(term.casefold(), [])
        if not matches:
            out.warnings.append(f"No location matched '{term}'.")
            continue
        roots[sign].extend(id_ for id_, _, _ in matches)
        names.append(sign + matches[0][1])
        _remember(out, "locations", term, matches)
    if names:
        out.labels["locations"] = names

    def with_descendants(ids: list[str]) -> list[str]:
        if not ids:
            return []
        rows = con.execute(
            """SELECT DISTINCT a.id FROM areas a, (SELECT unnest($roots::VARCHAR[]) AS root) r
               WHERE a.id = r.root OR starts_with(a.id, r.root || '/')""",
            {"roots": ids},
        ).fetchall()
        return sorted({r[0] for r in rows} | set(ids))

    return with_descendants(roots[""]), with_descendants(roots["!"])


def _games_of_series(con: duckdb.DuckDBPyConnection, series_ids: list[str]) -> list[str]:
    if not series_ids:
        return []
    rows = con.execute(
        "SELECT DISTINCT game_id FROM game_series WHERE series_id IN (SELECT unnest($ids::VARCHAR[]))",
        {"ids": series_ids},
    ).fetchall()
    return [r[0] for r in rows]


def resolve(con: duckdb.DuckDBPyConnection, spec: FilterSpec) -> ResolvedFilter:
    out = ResolvedFilter(warnings=list(spec.warnings))

    inc_series, exc_series = _resolve_box(con, "series", spec.series, out)
    inc_games, exc_games = _resolve_box(con, "games", spec.games, out)
    out.inc_games = sorted(set(_games_of_series(con, inc_series)) | set(inc_games))
    out.exc_games = sorted(set(_games_of_series(con, exc_series)) | set(exc_games))
    out.inc_platforms, out.exc_platforms = _resolve_box(con, "platforms", spec.platforms, out)
    out.inc_players, out.exc_players = _resolve_box(con, "players", spec.players, out)
    out.inc_areas, out.exc_areas = _resolve_locations(con, spec.locations, out)

    # An include-term that matched nothing still narrows the scope (to nothing) rather than widening it to "all".
    out.all_scope = not spec.has_scope_terms
    out.all_players = not spec.players.include
    out.all_locations = not spec.locations.include
    out.single_player = len(spec.players.include) == 1
    return out
