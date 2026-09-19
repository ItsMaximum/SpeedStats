# SpeedStats

Scrapes all of [speedrun.com](https://www.speedrun.com/), assigns a point value to every run
([formula](https://www.desmos.com/calculator/uvredthnmv)), and serves the rankings at
[speedstats.app](https://speedstats.app/).

```
scraper/   crawls speedrun.com into DuckDB, scores it (score.sql), validates and publishes one database file
api/       FastAPI: the seven query types, autocomplete, flags; serves the built web app
web/       Vite + React + TypeScript front end; all state lives in the URL, so every view is a shareable link
speedstats/ shared: settings, query-string parsing (mirrored in web/src/query.ts)
```

## How it works

1. **Crawl** (`python -m scraper crawl`): series list -> series games -> all games -> for each game, its data and
   every leaderboard page, through the Heroku proxies in production. Rows are appended to
   `data/work/crawl-<version>.duckdb` as they arrive (a few hundred MB of RAM, not tens of GB) and progress is
   checkpointed per game, so `--resume` continues a crashed crawl.
2. **Score** (`python -m scraper score`): `normalize.sql` + `score.sql` reproduce the original Python scoring
   exactly (verified row by row against the old pipeline; `tests/test_scoring.py` pins that output as golden
   files) and write `data/speedstats-<version>.duckdb`.
3. **Validate + publish**: sanity gates (row counts vs last week, top-100 overlap, smoke queries...). Only when
   they pass does `data/CURRENT` move to the new file; the API notices within 10 s and hot-swaps, the Cloudflare
   cache is purged, and a parquet snapshot goes to R2. If they fail, last week's data keeps serving.
4. **Serve**: the API opens the file read-only; queries run in DuckDB in milliseconds and Cloudflare caches
   responses for the week.

`python -m scraper all --resume` does 1-3 and is what the weekly GitHub Actions job runs.

## Local development

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 22.

```bash
uv sync
npm install               # also installs web/
cp .env.example .env      # optional; defaults work
```

Get a database (any of these writes `data/CURRENT`):

```bash
uv run python -m scraper fixture-db                                   # the Fancy Pants series (checked-in crawl), instant
uv run python -m scraper crawl --only-series 643gq07w && uv run python -m scraper score   # one series, live data
uv run python -m scraper bootstrap                                    # latest published snapshot from R2
```

Then:

```bash
npm run dev          # API on :8000 (auto-reload) + web on :5173 (HMR); open http://localhost:5173
npm run dev:live     # web only, talking to the deployed API at new.speedstats.app
npm run check        # ruff + pytest + tsc + vitest + vite build, same as CI
npm run gen-types    # regenerate web/src/api-types.d.ts after changing the API models
uv run python tools/update_golden.py   # after an intentional scoring change: refresh tests/fixtures/expected-*.csv
```

Local crawls use direct requests (no `SRC_PROXIES` in `.env`); keep them to a series or a game.

To compare against the live site for a set of real URLs (`tests/regression/urls.txt`):

```bash
uv run python tools/regression.py capture && uv run python tools/regression.py check --db data/<file>.duckdb
```

## URLs

Old links keep working: `/index.php?series=&games=Red+Ball&platforms=&players=&request-type=pr` (terms separated
by `", "`). New links use `v=2` with one param per term (`games=A&games=B`), so names containing commas work.
Every box accepts a name or the speedrun.com abbreviation (`redball1`, `fpa`, `us`, `england`); a leading `-`
excludes (`series=Red Ball&games=-Red Ball 5`). Players and countries narrow the result; series, games and
platforms combine.

`/api/query` returns JSON (`format=csv` for CSV), `/api/suggest` powers autocomplete, `/api/meta` and `/health`
report the data version. OpenAPI docs at `/api/docs`.

## Production

Everything runs on the Oracle VM under `/opt/speedstats` with Docker Compose (`docker-compose.yml`), behind the
existing Cloudflare Tunnel. Nothing needs ssh after the one-time `ops/vm-setup.sh`:

| what | where |
|---|---|
| deploy | push to `main` -> `deploy.yml` builds the image and the self-hosted runner restarts the API |
| weekly scrape | `scrape.yml`, Sundays 2 AM Eastern (or *Run workflow*); logs, re-run and a summary in the Actions tab |
| container logs | `logs.speedstats.app` (Dozzle, behind Cloudflare Access) |
| traffic, cache, tunnel | Cloudflare dashboard |
| is the data fresh? | `/health` reports `data_version`, `scraped_at` and `stale` (older than 9 days); 503 until the first publish. `/healthz` is plain liveness |

Secrets (proxy names, excluded players, Cloudflare and R2 tokens) are GitHub Actions secrets; the Deploy
workflow writes them to `/opt/speedstats/.env` on the VM. Non-secret settings (`PUBLIC_URL`, `R2_BUCKET`, ...) are
repository variables. Change one, re-run Deploy. See `.env.example` for the full list.

### Cut-over plan

1. `new.speedstats.app` points at the new API while `speedstats.app` keeps serving the PHP site.
2. The first scrape runs Sunday 2 AM ET; compare both sites (`tools/regression.py` against the same crawl).
3. When happy: change the tunnel route for `speedstats.app` to `http://localhost:8000` and set
   `PUBLIC_URL=https://speedstats.app`. Rollback is switching the route back.
4. After a clean week: stop Apache/PHP and MariaDB, retire the old repos.

## License

[GPL-3.0-or-later](LICENSE), like the previous SpeedStats repositories.
