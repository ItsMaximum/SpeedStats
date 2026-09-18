-- Intermediate tables consumed by score.sql. Produced from a crawl database by normalize.sql,
-- or from a legacy SpeedStats-V3 runs.json by legacy.py (where ids are names).

CREATE OR REPLACE TABLE games_d       (id VARCHAR, name VARCHAR, url VARCHAR, default_timer TINYINT);
CREATE OR REPLACE TABLE series_d      (id VARCHAR, name VARCHAR, url VARCHAR);
CREATE OR REPLACE TABLE game_series_d (game_id VARCHAR, series_id VARCHAR);
CREATE OR REPLACE TABLE platforms_d   (id VARCHAR, name VARCHAR, url VARCHAR);
CREATE OR REPLACE TABLE areas_d       (id VARCHAR, name VARCHAR, full_name VARCHAR, lb_name VARCHAR, lb_flag VARCHAR,
                                       parent_id VARCHAR);  -- lb_flag: area id whose flag speedrun.com shows on boards
CREATE OR REPLACE TABLE players_d     (id VARCHAR, name VARCHAR, url VARCHAR, area_id VARCHAR, country VARCHAR,
                                       flag VARCHAR, is_guest BOOLEAN);  -- country: ISO top level; flag: lb_flag of area
CREATE OR REPLACE TABLE scored_input (
    ord              BIGINT,        -- insertion order (Python's stable-sort tiebreak)
    run_id           VARCHAR,
    leaderboard_name VARCHAR,
    game_id          VARCHAR,
    platform_id      VARCHAR,       -- NULL when unknown
    player_ids       VARCHAR[],     -- ordered as listed on the run; may contain NULL (unknown player)
    is_reverse       BOOLEAN,
    t                DOUBLE,        -- selected time (Run.getTime), NULL when the run has no usable time
    date             BIGINT,        -- unix seconds, 0 when unknown
    date_submitted   BIGINT,        -- unix seconds, 2147483647 when unknown
    is_level_run     BOOLEAN
);
