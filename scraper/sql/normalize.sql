-- Raw crawl tables -> intermediate tables (see intermediate_schema.sql), reproducing SpeedStats-V3/scraperuns.py:
-- getGroupName() builds the leaderboard name, Run.getTime() picks the time, guests are 38-character ids.
-- Lookup tables may hold duplicates from re-fetches; the latest seen_at wins.

CREATE OR REPLACE TABLE games_d AS
SELECT id, name, url, default_timer
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_games) WHERE rn = 1;

CREATE OR REPLACE TABLE series_d AS
SELECT id, name, url
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_series) WHERE rn = 1;

CREATE OR REPLACE TABLE game_series_d AS
SELECT DISTINCT game_id, series_id FROM raw_game_series;

CREATE OR REPLACE TABLE platforms_d AS
SELECT id, name, url
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_platforms) WHERE rn = 1;

CREATE OR REPLACE TABLE areas_d AS
SELECT id, name, full_name, lb_name, lb_flag, parent_id
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_areas) WHERE rn = 1;

CREATE OR REPLACE TEMP TABLE colors_d AS
SELECT id, dark
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_colors) WHERE rn = 1;

-- name colours as speedrun.com shows them in dark mode; two colours mean a gradient
CREATE OR REPLACE TABLE players_d AS
SELECT p.id, p.name, p.url, p.area_id,
       nullif(split_part(p.area_id, '/', 1), '') AS country,
       a.lb_flag                                  AS flag,
       c1.dark                                    AS color1,
       c2.dark                                    AS color2,
       length(p.id) = 38                          AS is_guest
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_players) p
LEFT JOIN areas_d a ON a.id = p.area_id
LEFT JOIN colors_d c1 ON c1.id = p.color1_id
LEFT JOIN colors_d c2 ON c2.id = p.color2_id
WHERE rn = 1;

-- categories / levels / subcategory values, latest per id
CREATE OR REPLACE TEMP TABLE categories_d AS
SELECT id, game_id, name, time_direction
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_categories) WHERE rn = 1;

CREATE OR REPLACE TEMP TABLE levels_d AS
SELECT id, name
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_levels) WHERE rn = 1;

-- only values of variables that are subcategories and not archived take part in the leaderboard name
CREATE OR REPLACE TEMP TABLE subcat_values AS
SELECT v.id, v.name
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_values) v
JOIN (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY seen_at DESC) AS rn FROM raw_variables) var
  ON var.id = v.variable_id AND var.rn = 1
WHERE v.rn = 1 AND var.is_subcategory AND NOT var.archived;

-- runs of games that finished cleanly; a run seen on two pages (board shifted mid-crawl) is kept once
CREATE OR REPLACE TEMP TABLE runs_d AS
SELECT * FROM raw_runs
WHERE game_id IN (SELECT game_id FROM crawl_checkpoint WHERE status = 'done')
QUALIFY ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY ord) = 1;

CREATE OR REPLACE TEMP TABLE run_subcats AS
SELECT run_id, string_agg(sv.name, ', ' ORDER BY u.pos) AS subcat_text
FROM (SELECT run_id, unnest(value_ids) AS value_id, generate_subscripts(value_ids, 1) AS pos FROM runs_d) u
JOIN subcat_values sv ON sv.id = u.value_id
GROUP BY run_id;

CREATE OR REPLACE TABLE scored_input AS
SELECT
    r.ord, r.run_id, r.game_id, r.platform_id, r.player_ids,
    g.name || ': ' || c.name
      || CASE WHEN r.level_id IS NOT NULL THEN ', ' || l.name ELSE '' END
      || CASE WHEN rs.subcat_text IS NOT NULL THEN ' - ' || rs.subcat_text ELSE '' END AS leaderboard_name,
    c.time_direction = 1 AS is_reverse,
    CASE WHEN g.default_timer IN (0, 1)
         THEN coalesce(r.time, r.time_with_loads, r.igt + 10000000.0)   -- RTA/LRT games: IGT-only runs sort last
         ELSE coalesce(r.igt, r.time, r.time_with_loads) END AS t,
    coalesce(r.date, 0)                    AS date,
    coalesce(r.date_submitted, 2147483647) AS date_submitted,
    r.level_id IS NOT NULL                 AS is_level_run
FROM runs_d r
JOIN games_d g      ON g.id = r.game_id
JOIN categories_d c ON c.id = r.category_id
LEFT JOIN levels_d l    ON l.id = r.level_id
LEFT JOIN run_subcats rs ON rs.run_id = r.run_id
ORDER BY r.ord;
