"""Turn the free-text terms of a FilterSpec into sets of ids, using the published lookup tables.

Every term matches a full name or a speedrun.com slug, case-insensitively. Countries also match the ISO code
or the flag label. Unmatched terms become warnings and are never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

from speedstats.filters import BoxTerms, FilterSpec

BOX_LABELS = {
    "series": "series",
    "games": "game",
    "platforms": "platform",
    "players": "player",
    "countries": "country",
}

_LOOKUP_SQL = {
    "games": "SELECT t.term, x.id, x.name FROM terms t JOIN games x ON x.name_lower = t.term OR x.slug_lower = t.term",
    "series": "SELECT t.term, x.id, x.name FROM terms t JOIN series x ON x.name_lower = t.term OR x.slug_lower = t.term",
    "platforms": (
        "SELECT t.term, x.id, x.name FROM terms t JOIN platforms x ON x.name_lower = t.term OR x.slug_lower = t.term"
    ),
    "players": "SELECT t.term, x.id, x.name FROM terms t JOIN players x ON x.name_lower = t.term OR x.slug_lower = t.term",
    "countries": (
        "SELECT t.term, x.id, x.name FROM terms t JOIN areas x ON x.is_country "
        "AND (x.id_lower = t.term OR x.name_lower = t.term OR lower(x.label) = t.term)"
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
    inc_countries: list[str] = field(default_factory=list)
    exc_countries: list[str] = field(default_factory=list)
    all_scope: bool = True  # no series/games/platforms include-term given
    all_players: bool = True  # no players include-term given
    all_countries: bool = True  # no countries include-term given
    single_player: bool = False  # exactly one player include-term: tables drop the Player column (as before)
    labels: dict[str, list[str]] = field(default_factory=dict)  # canonical display names per box, for titles
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
            "inc_countries": self.inc_countries,
            "exc_countries": self.exc_countries,
            "all_scope": self.all_scope,
            "all_players": self.all_players,
            "all_countries": self.all_countries,
            "limit": limit,
        }


def _lookup(con: duckdb.DuckDBPyConnection, box: str, terms: tuple[str, ...]) -> dict[str, list[tuple[str, str]]]:
    """term (casefolded) -> [(id, name), ...]"""
    if not terms:
        return {}
    con.execute(
        "CREATE OR REPLACE TEMP TABLE terms AS SELECT DISTINCT unnest($terms::VARCHAR[]) AS term",
        {"terms": [t.casefold() for t in terms]},
    )
    found: dict[str, list[tuple[str, str]]] = {}
    for term, id_, name in con.execute(_LOOKUP_SQL[box]).fetchall():
        found.setdefault(term, []).append((id_, name))
    return found


def _resolve_box(
    con: duckdb.DuckDBPyConnection, box: str, terms: BoxTerms, out: ResolvedFilter
) -> tuple[list[str], list[str]]:
    hits = _lookup(con, box, terms.include + terms.exclude)
    label = BOX_LABELS[box]
    names: list[str] = []

    def ids_for(term_list: tuple[str, ...], sign: str) -> list[str]:
        ids: list[str] = []
        for term in term_list:
            matches = hits.get(term.casefold(), [])
            if not matches:
                out.warnings.append(f"No {label} matched '{term}'.")
                continue
            ids.extend(id_ for id_, _ in matches)
            names.append(sign + matches[0][1])
        return ids

    include = ids_for(terms.include, "")
    exclude = ids_for(terms.exclude, "-")
    if names:
        out.labels[box] = names
    return include, exclude


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
    out.inc_countries, out.exc_countries = _resolve_box(con, "countries", spec.countries, out)

    # An include-term that matched nothing still narrows the scope (to nothing) rather than widening it to "all".
    out.all_scope = not spec.has_scope_terms
    out.all_players = not spec.players.include
    out.all_countries = not spec.countries.include
    out.single_player = len(spec.players.include) == 1
    return out
