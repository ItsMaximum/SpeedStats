"""The seven request types as DuckDB SQL over the published `runs` / `player_ranks` tables."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from api.resolve import ResolvedFilter

# One scope for every query: (series ∪ games ∪ platforms) minus exclusions, restricted by players and countries.
SCOPE_CTE = """
WITH scope AS (
    SELECT r.* FROM runs r
    WHERE ($all_scope
           OR r.game_id     IN (SELECT unnest($inc_games::VARCHAR[]))
           OR r.platform_id IN (SELECT unnest($inc_platforms::VARCHAR[])))
      AND r.game_id NOT IN (SELECT unnest($exc_games::VARCHAR[]))
      AND (r.platform_id IS NULL OR r.platform_id NOT IN (SELECT unnest($exc_platforms::VARCHAR[])))
      AND ($all_players OR r.player_id IN (SELECT unnest($inc_players::VARCHAR[])))
      AND r.player_id NOT IN (SELECT unnest($exc_players::VARCHAR[]))
      AND ($all_countries OR r.country IN (SELECT unnest($inc_countries::VARCHAR[]))
                             OR r.flag IN (SELECT unnest($inc_countries::VARCHAR[])))
      AND (r.country IS NULL OR r.country NOT IN (SELECT unnest($exc_countries::VARCHAR[])))
      AND (r.flag IS NULL OR r.flag NOT IN (SELECT unnest($exc_countries::VARCHAR[])))
)
"""

# Player Rankings over all games come straight from the precomputed table. Global rank is kept when only
# players are named (a "where do these players stand" lookup); a country filter re-numbers ("US rankings").
PR_PRECOMPUTED = """
SELECT {rank} AS "Rank", player AS "Player", flag AS "Flag", round(points, 2) AS "Points"
FROM player_ranks p
WHERE ($all_players OR p.player_id IN (SELECT unnest($inc_players::VARCHAR[])))
  AND p.player_id NOT IN (SELECT unnest($exc_players::VARCHAR[]))
  AND ($all_countries OR p.country IN (SELECT unnest($inc_countries::VARCHAR[]))
                       OR p.flag IN (SELECT unnest($inc_countries::VARCHAR[])))
  AND (p.country IS NULL OR p.country NOT IN (SELECT unnest($exc_countries::VARCHAR[])))
  AND (p.flag IS NULL OR p.flag NOT IN (SELECT unnest($exc_countries::VARCHAR[])))
ORDER BY rank LIMIT $limit
"""

PR_SCOPED = (
    SCOPE_CTE
    + """,
ranked AS (
    SELECT player_id, player, flag, value,
           ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY value DESC) AS pr
    FROM scope
),
pts AS (
    SELECT player_id, any_value(player) AS player, any_value(flag) AS flag,
           SUM(GREATEST(value * POWER(0.99, pr - 1), value * 0.25)) AS points
    FROM ranked GROUP BY player_id
)
SELECT ROW_NUMBER() OVER (ORDER BY points DESC, player) AS "Rank", player AS "Player", flag AS "Flag",
       round(points, 2) AS "Points"
FROM pts ORDER BY points DESC, player LIMIT $limit
"""
)

RUNS = (
    SCOPE_CTE
    + """
SELECT ROW_NUMBER() OVER (ORDER BY value DESC, leaderboard, player) AS "Rank", {player_cols}
       leaderboard AS "Leaderboard", place AS "Place", value AS "Value"
FROM scope ORDER BY value DESC, leaderboard, player LIMIT $limit
"""
)

# Most Valuable Records: the best credited row of each leaderboard.
RECORDS = (
    SCOPE_CTE
    + """,
best AS (
    SELECT * FROM scope
    QUALIFY ROW_NUMBER() OVER (PARTITION BY leaderboard_id ORDER BY value DESC, place, player) = 1
)
SELECT ROW_NUMBER() OVER (ORDER BY value DESC, leaderboard) AS "Rank", leaderboard AS "Leaderboard", {player_cols}
       value AS "Value"
FROM best ORDER BY value DESC, leaderboard LIMIT $limit
"""
)

GROUPED = (
    SCOPE_CTE
    + """
SELECT ROW_NUMBER() OVER (ORDER BY points DESC, k) AS "Rank", k AS "{label}", round(points, 2) AS "Points"
FROM (SELECT {column} AS k, SUM(value) AS points FROM scope WHERE {column} IS NOT NULL GROUP BY 1)
ORDER BY points DESC, k LIMIT $limit
"""
)

# A game in several series counts towards each of them.
SERIES = (
    SCOPE_CTE
    + """
SELECT ROW_NUMBER() OVER (ORDER BY points DESC, series) AS "Rank", series AS "Series", round(points, 2) AS "Points"
FROM (SELECT s.name AS series, SUM(r.value) AS points
      FROM scope r JOIN game_series gs ON gs.game_id = r.game_id JOIN series s ON s.id = gs.series_id
      GROUP BY s.id, s.name)
ORDER BY points DESC, series LIMIT $limit
"""
)

GROUP_COLUMNS = {"leaderboards": ("leaderboard", "Leaderboard"), "games": ("game", "Game"), "dates": ("date", "Date")}

REQUEST_TYPE_NAMES = {
    "pr": "Player Rankings",
    "runs": "Runs by Player(s)",
    "records": "Most Valuable Records",
    "leaderboards": "Leaderboard Value",
    "games": "Game Value",
    "series": "Series Value",
    "dates": "Date Value",
}


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[object]]
    truncated: bool


def build_sql(request_type: str, filt: ResolvedFilter) -> str:
    player_cols = "" if filt.single_player else 'player AS "Player", flag AS "Flag",'
    match request_type:
        case "pr":
            if filt.unfiltered_ranking:
                rank = "rank" if filt.all_countries else "ROW_NUMBER() OVER (ORDER BY rank)"
                return PR_PRECOMPUTED.format(rank=rank)
            return PR_SCOPED
        case "runs":
            return RUNS.format(player_cols=player_cols)
        case "records":
            return RECORDS.format(player_cols=player_cols)
        case "series":
            return SERIES
        case _:
            column, label = GROUP_COLUMNS[request_type]
            return GROUPED.format(column=column, label=label)


def run_query(con: duckdb.DuckDBPyConnection, request_type: str, filt: ResolvedFilter, limit: int) -> QueryResult:
    sql = build_sql(request_type, filt)
    params = {k: v for k, v in filt.params(limit + 1).items() if f"${k}" in sql}  # DuckDB rejects unused params
    cur = con.execute(sql, params)
    columns = [d[0] for d in cur.description]
    rows = [list(r) for r in cur.fetchall()]
    truncated = len(rows) > limit
    return QueryResult(columns, rows[:limit], truncated)
