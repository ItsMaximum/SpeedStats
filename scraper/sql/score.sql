-- Scores every leaderboard and writes the published tables into the attached `pub` database.
--
-- Input (regular tables in the work database, produced by normalize.sql or legacy.py):
--   games_d(id, name, url, default_timer)      series_d(id, name, url)       game_series_d(game_id, series_id)
--   platforms_d(id, name, url)                 areas_d(id, name, full_name, label)
--   players_d(id, name, url, area_id, country, is_guest)
--   scored_input(ord, run_id, leaderboard_name, game_id, platform_id, player_ids, is_reverse, t, date,
--                date_submitted, is_level_run)
--   excluded_players(name)                     score_params(key, value)   -- data_version, scraped_at, errored_games
--
-- This is a line-by-line port of SpeedStats-V3/processruns.py; comments reference the Python it reproduces.
-- `ord` is insertion order, which is the tiebreak Python's stable sorts fall back to.

-- buildLeaderboard(): stable sort by (date, dateSubmitted), then stable sort by time (desc when reverse),
-- keep the first run per ordered player list.
CREATE OR REPLACE TABLE lb AS
WITH o AS (
    SELECT *, CASE WHEN is_reverse THEN -t ELSE t END AS t_ord
    FROM scored_input
    WHERE t IS NOT NULL
),
d AS (
    SELECT * FROM o
    QUALIFY ROW_NUMBER() OVER (PARTITION BY leaderboard_name, player_ids
                               ORDER BY t_ord, date, date_submitted, ord) = 1
)
SELECT *,
    ROW_NUMBER() OVER (PARTITION BY leaderboard_name ORDER BY t_ord, date, date_submitted, ord) AS nominal_place,
    RANK()       OVER (PARTITION BY leaderboard_name ORDER BY t_ord)                            AS place,
    COUNT(*)     OVER (PARTITION BY leaderboard_name)                                           AS leaderboard_runs
FROM d;

CREATE OR REPLACE TABLE lb_stats AS
WITH totals AS (                                      -- len(runs)
    SELECT leaderboard_name, COUNT(*) AS total_runs
    FROM scored_input WHERE t IS NOT NULL GROUP BY 1
),
wr_walk AS (                                          -- findNumWRs(): walk (date, dateSubmitted) order, date > 0 only
    SELECT leaderboard_name, t_ord,
           MIN(t_ord) OVER (PARTITION BY leaderboard_name ORDER BY date, date_submitted, ord
                            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_best
    FROM (SELECT leaderboard_name, ord, date, date_submitted,
                 CASE WHEN is_reverse THEN -t ELSE t END AS t_ord
          FROM scored_input WHERE t IS NOT NULL AND date > 0)
),
wrs AS (
    SELECT leaderboard_name, COUNT(*) FILTER (WHERE prev_best IS NULL OR t_ord < prev_best) AS num_wrs
    FROM wr_walk GROUP BY 1
),
median AS (                                           -- leaderboard[(leaderboardRuns - 1) // 2]['time']
    SELECT leaderboard_name, t AS median_t
    FROM lb WHERE nominal_place = ((leaderboard_runs - 1) // 2) + 1
),
base AS (
    SELECT l.leaderboard_name, tot.total_runs, l.leaderboard_runs, coalesce(w.num_wrs, 0) AS num_wrs,
           (m.median_t % 10000000.0) / 60.0 AS run_length
    FROM (SELECT DISTINCT leaderboard_name, leaderboard_runs FROM lb) l
    JOIN totals tot USING (leaderboard_name)
    LEFT JOIN wrs w USING (leaderboard_name)
    JOIN median m USING (leaderboard_name)
),
weighted AS (
    SELECT *, 1.1 - power(1.01, -(run_length + 200)) - power(2.4, -(run_length + 1.2)) AS length_weight
    FROM base
)
SELECT *,
    -- WRValue: math.log(x, b) is log(x)/log(b)
    ((ln(total_runs::DOUBLE) / ln(1.7)) * num_wrs + 120 * exp(-100.0 / total_runs) + 0.04 * total_runs)
      * (1 - (num_wrs + 1)::DOUBLE / (total_runs + leaderboard_runs))
      * length_weight                                                                        AS wr_value,
    CASE WHEN leaderboard_runs > 2
         THEN (ln(leaderboard_runs::DOUBLE) / ln(10.0)) / leaderboard_runs + 0.001
         ELSE 0.2 END                                                                        AS sf
FROM weighted;

-- per-run value at its nominal place (x0.75 for level runs), then averaged across a tie
CREATE OR REPLACE TABLE lb_valued AS
WITH v AS (
    SELECT r.*,
        ((s.sf * s.wr_value) * (s.leaderboard_runs + 1 - r.nominal_place))
          / (r.nominal_place + (s.sf * s.leaderboard_runs - 1))
          * CASE WHEN r.is_level_run THEN 0.75 ELSE 1.0 END AS nominal_value
    FROM lb r JOIN lb_stats s USING (leaderboard_name)
)
SELECT *, AVG(nominal_value) OVER (PARTITION BY leaderboard_name, place) AS run_value FROM v;

-- generateCSV(): split the value across all listed players, credit each player once per leaderboard
-- (their first = best appearance), skip guests, unknown names and excluded players.
CREATE TABLE pub.runs AS
WITH exploded AS (
    SELECT v.leaderboard_name, v.game_id, v.nominal_place, v.place, v.run_value, v.platform_id, v.date,
           len(v.player_ids) AS n_players,
           unnest(v.player_ids) AS player_id, generate_subscripts(v.player_ids, 1) AS player_ord
    FROM lb_valued v
),
credited AS (
    SELECT e.*, p.name AS player, p.country
    FROM exploded e
    JOIN players_d p ON p.id = e.player_id
    WHERE NOT p.is_guest
      AND p.name NOT IN (SELECT name FROM excluded_players)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY e.leaderboard_name, e.player_id ORDER BY e.nominal_place, e.player_ord) = 1
)
SELECT
    dense_rank() OVER (ORDER BY c.leaderboard_name)::INTEGER AS leaderboard_id,
    c.leaderboard_name                                       AS leaderboard,
    c.game_id, g.name                                        AS game,
    c.player_id, c.player, c.country,
    c.platform_id, pl.name                                   AS platform,
    c.place::INTEGER                                         AS place,
    round(c.run_value / c.n_players, 3)                      AS value,
    CASE WHEN c.date > 0 THEN epoch_ms(c.date * 1000)::DATE END AS date
FROM credited c
JOIN games_d g ON g.id = c.game_id
LEFT JOIN platforms_d pl ON pl.id = c.platform_id
ORDER BY c.game_id, leaderboard_id, c.nominal_place;

CREATE TABLE pub.player_ranks AS
SELECT ROW_NUMBER() OVER (ORDER BY points DESC, player)::INTEGER AS rank, player_id, player, country, points
FROM (
    SELECT player_id, any_value(player) AS player, any_value(country) AS country,
           SUM(GREATEST(value * POWER(0.99, player_rank - 1), value * 0.25)) AS points
    FROM (SELECT player_id, player, country, value,
                 ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY value DESC) AS player_rank
          FROM pub.runs)
    GROUP BY player_id
)
ORDER BY rank;

CREATE TABLE pub.games AS
SELECT id, name, url AS slug, lower(name) AS name_lower, lower(url) AS slug_lower, default_timer
FROM games_d WHERE id IN (SELECT DISTINCT game_id FROM pub.runs) ORDER BY name_lower;

CREATE TABLE pub.series AS
SELECT id, name, url AS slug, lower(name) AS name_lower, lower(url) AS slug_lower
FROM series_d ORDER BY name_lower;

CREATE TABLE pub.game_series AS
SELECT DISTINCT game_id, series_id FROM game_series_d
WHERE game_id IN (SELECT id FROM pub.games) AND series_id IN (SELECT id FROM pub.series);

CREATE TABLE pub.platforms AS
SELECT id, name, url AS slug, lower(name) AS name_lower, lower(url) AS slug_lower
FROM platforms_d ORDER BY name_lower;

CREATE TABLE pub.areas AS
SELECT id, name, full_name, label, NOT contains(id, '/') AS is_country, lower(id) AS id_lower, lower(name) AS name_lower
FROM areas_d ORDER BY id_lower;

CREATE TABLE pub.players AS
SELECT id, name, url AS slug, area_id, country, lower(name) AS name_lower, lower(url) AS slug_lower
FROM players_d WHERE id IN (SELECT DISTINCT player_id FROM pub.runs) ORDER BY name_lower;
CREATE INDEX players_name_lower_idx ON pub.players(name_lower);
CREATE INDEX players_slug_lower_idx ON pub.players(slug_lower);

CREATE TABLE pub.meta AS
SELECT
    1                                                                       AS schema_version,
    (SELECT value FROM score_params WHERE key = 'data_version')             AS data_version,
    (SELECT value FROM score_params WHERE key = 'scraped_at')::TIMESTAMP    AS scraped_at,
    now()::TIMESTAMP                                                        AS published_at,
    (SELECT COUNT(*) FROM pub.runs)                                         AS row_count,
    (SELECT COUNT(*) FROM scored_input)                                     AS raw_run_count,
    (SELECT COUNT(*) FROM pub.games)                                        AS game_count,
    (SELECT COUNT(DISTINCT leaderboard_id) FROM pub.runs)                   AS leaderboard_count,
    (SELECT COUNT(*) FROM pub.players)                                      AS player_count,
    coalesce((SELECT value FROM score_params WHERE key = 'errored_games'), '0')::INTEGER AS errored_games;
