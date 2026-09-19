-- Raw crawl store: append-only, no keys or indexes. Re-fetches produce duplicates that normalize.sql collapses
-- (latest seen_at wins). One file per crawl: work/crawl-<version>.duckdb.

CREATE TABLE IF NOT EXISTS crawl_meta (key VARCHAR PRIMARY KEY, value VARCHAR);  -- version, started_at, stage

CREATE TABLE IF NOT EXISTS raw_series      (id VARCHAR, name VARCHAR, url VARCHAR, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_game_list   (id VARCHAR, name VARCHAR, url VARCHAR, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_game_series (game_id VARCHAR, series_id VARCHAR, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_games       (id VARCHAR, name VARCHAR, url VARCHAR, default_timer TINYINT, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_categories  (id VARCHAR, game_id VARCHAR, name VARCHAR, time_direction TINYINT,
                                            archived BOOLEAN, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_levels      (id VARCHAR, game_id VARCHAR, name VARCHAR, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_variables   (id VARCHAR, game_id VARCHAR, name VARCHAR, is_subcategory BOOLEAN,
                                            archived BOOLEAN, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_values      (id VARCHAR, variable_id VARCHAR, game_id VARCHAR, name VARCHAR,
                                            seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_platforms   (id VARCHAR, name VARCHAR, url VARCHAR, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_areas       (id VARCHAR, name VARCHAR, full_name VARCHAR, lb_name VARCHAR,
                                            lb_flag VARCHAR, parent_id VARCHAR, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_colors      (id VARCHAR, name VARCHAR, dark VARCHAR, light VARCHAR, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_players     (id VARCHAR, name VARCHAR, url VARCHAR, area_id VARCHAR, color1_id VARCHAR,
                                            color2_id VARCHAR, seen_at TIMESTAMP);

CREATE TABLE IF NOT EXISTS raw_runs (
    ord             BIGINT,          -- insertion order
    run_id          VARCHAR,
    game_id         VARCHAR,
    category_id     VARCHAR,
    level_id        VARCHAR,         -- NULL for full-game runs
    value_ids       VARCHAR[],       -- as returned, in order
    player_ids      VARCHAR[],       -- as returned, in order
    platform_id     VARCHAR,
    time            DOUBLE,          -- seconds; NULL when absent
    time_with_loads DOUBLE,
    igt             DOUBLE,
    date            BIGINT,          -- unix seconds; 0 or NULL when unknown
    date_submitted  BIGINT,
    lb_type         TINYINT,         -- 1 = GetGameLeaderboard, 2 = GetGameLeaderboard2
    page            INTEGER
);

-- Resume unit. A row is written only after every row of the game has been queued before it (FIFO writer).
CREATE TABLE IF NOT EXISTS crawl_checkpoint (
    game_id     VARCHAR PRIMARY KEY,
    status      VARCHAR,             -- done | error | skipped
    categories  INTEGER,
    pages       INTEGER,
    runs        INTEGER,
    error       VARCHAR,
    finished_at TIMESTAMP
);
