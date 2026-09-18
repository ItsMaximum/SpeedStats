"""SpeedStats API + SPA host.

/api/query    the seven request types (JSON, or CSV with format=csv)
/api/suggest  autocomplete for the filter boxes
/api/meta     data version / last update
/health       liveness + staleness for monitoring
/*            the built React app (web/dist), with per-query <title>/og: tags injected for link previews
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import html
import io
import logging
import re
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from threading import Lock
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from api.db import DbHolder, Meta, NoDatabase
from api.queries import REQUEST_TYPE_NAMES, QueryResult, run_query
from api.resolve import ResolvedFilter, resolve
from api.schemas import HealthOut, MetaOut, QueryOut, Suggestion, SuggestOut
from speedstats.config import settings
from speedstats.filters import BOXES, FilterSpec, parse_query, to_params

log = logging.getLogger("speedstats.api")

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
CACHE_CONTROL = "public, max-age=300, s-maxage=604800"
IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_ENTRIES = 2000
FLAG_SOURCE = "https://www.speedrun.com/images/flags/"
FLAG_ID = re.compile(r"^[a-z0-9_-]+(?:/[a-z0-9_-]+)*$")

holder = DbHolder(settings.data_dir)


class ResultCache:
    """Small in-process LRU keyed on (data version, canonical query); the edge cache does the heavy lifting."""

    def __init__(self, size: int):
        self.size = size
        self._lock = Lock()
        self._items: OrderedDict[tuple[str, str], object] = OrderedDict()

    def get(self, key: tuple[str, str]) -> object | None:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
            return None

    def put(self, key: tuple[str, str], value: object) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)


cache = ResultCache(CACHE_ENTRIES)


@asynccontextmanager
async def lifespan(app: FastAPI):
    holder.open()
    task = asyncio.create_task(holder.watch())
    try:
        yield
    finally:
        task.cancel()
        holder.close()


app = FastAPI(
    title="SpeedStats API", version="4.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json"
)


# -- helpers ---------------------------------------------------------------------------------------------------------


def meta_out(meta: Meta) -> MetaOut:
    return MetaOut(
        data_version=meta.data_version,
        scraped_at=meta.scraped_at,
        published_at=meta.published_at,
        row_count=meta.row_count,
        leaderboard_count=meta.leaderboard_count,
        player_count=meta.player_count,
        game_count=meta.game_count,
        stale=meta.stale,
    )


def describe(spec: FilterSpec, filt: ResolvedFilter | None = None) -> str:
    """Human title for a query, e.g. 'Player Rankings - Red Ball, -Red Ball 5'."""
    parts: list[str] = []
    for box in BOXES:
        if filt is not None and box in filt.labels:
            parts.extend(filt.labels[box])
        else:
            terms = spec.box(box)
            parts.extend(terms.include)
            parts.extend(f"-{t}" for t in terms.exclude)
    name = REQUEST_TYPE_NAMES[spec.request_type]
    return f"{name} - {', '.join(parts)}" if parts else f"{name} - all games"


def canonical_query(spec: FilterSpec) -> str:
    return urlencode(to_params(spec))


def cache_headers(meta: Meta, key: str) -> dict[str, str]:
    etag = hashlib.sha1(f"{meta.data_version}:{key}".encode()).hexdigest()[:20]
    return {
        "Cache-Control": CACHE_CONTROL,
        "ETag": f'"{meta.data_version}-{etag}"',
        "Last-Modified": meta.published_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "X-Data-Version": meta.data_version,
    }


def _serialize_cell(value: object) -> str | int | float | None:
    if isinstance(value, date):
        return value.isoformat()
    return value  # type: ignore[return-value]


def _execute(spec: FilterSpec) -> tuple[QueryOut, Meta]:
    meta = holder.meta
    key = (meta.data_version, canonical_query(spec))
    cached = cache.get(key)
    if cached is not None:
        return cached, meta  # type: ignore[return-value]

    con = holder.cursor()
    try:
        filt = resolve(con, spec)
        result: QueryResult = run_query(con, spec.request_type, filt, spec.limit)
        flags = _flag_names(con, result)
    finally:
        con.close()

    out = QueryOut(
        request_type=spec.request_type,
        title=describe(spec, filt),
        columns=result.columns,
        rows=[[_serialize_cell(c) for c in row] for row in result.rows],
        truncated=result.truncated,
        warnings=filt.warnings,
        flags=flags,
        meta=meta_out(meta),
    )
    cache.put(key, out)
    return out, meta


def _flag_names(con, result: QueryResult) -> dict[str, str]:
    if "Flag" not in result.columns:
        return {}
    i = result.columns.index("Flag")
    ids = sorted({row[i] for row in result.rows if row[i]})
    if not ids:
        return {}
    rows = con.execute("SELECT id, lb_name FROM areas WHERE id IN (SELECT unnest($ids::VARCHAR[]))", {"ids": ids})
    return dict(rows.fetchall())


# -- routes ----------------------------------------------------------------------------------------------------------


@app.get("/api/query", response_model=QueryOut)
def api_query(request: Request, format: str = Query("json", pattern="^(json|csv)$")):
    spec = parse_query(request.query_params.multi_items())
    try:
        out, meta = _execute(spec)
    except NoDatabase as e:
        raise HTTPException(503, "no data published yet") from e
    headers = cache_headers(meta, canonical_query(spec))

    if format == "csv":

        def rows():
            buf = io.StringIO()
            writer = csv.writer(buf, lineterminator="\n")
            writer.writerow(out.columns)
            yield buf.getvalue()
            for row in out.rows:
                buf.seek(0)
                buf.truncate()
                writer.writerow(row)
                yield buf.getvalue()

        headers["Content-Disposition"] = 'attachment; filename="speedstats.csv"'
        return StreamingResponse(rows(), media_type="text/csv; charset=utf-8", headers=headers)

    return JSONResponse(out.model_dump(mode="json"), headers=headers)


_SUGGEST_TABLES = {
    "games": "games",
    "series": "series",
    "platforms": "platforms",
    "players": "players",
}


@app.get("/api/suggest", response_model=SuggestOut)
def api_suggest(box: str = Query(pattern="^(series|games|platforms|players|countries)$"), q: str = Query(min_length=1)):
    needle = q.strip().casefold().replace("%", r"\%").replace("_", r"\_")
    if not needle:
        return SuggestOut(box=box, q=q, items=[])
    try:
        con = holder.cursor()
    except NoDatabase as e:
        raise HTTPException(503, "no data published yet") from e
    try:
        if box == "countries":
            sql = """
                SELECT name, id FROM areas
                WHERE is_country AND (name_lower LIKE '%' || $q || '%' ESCAPE '\\' OR id_lower = $q)
                ORDER BY starts_with(name_lower, $q) DESC, length(name), name LIMIT 10"""
        else:
            sql = f"""
                SELECT name, slug FROM {_SUGGEST_TABLES[box]}
                WHERE name_lower LIKE '%' || $q || '%' ESCAPE '\\' OR slug_lower LIKE $q || '%' ESCAPE '\\'
                ORDER BY starts_with(name_lower, $q) DESC, starts_with(coalesce(slug_lower, ''), $q) DESC,
                         length(name), name LIMIT 10"""
        rows = con.execute(sql, {"q": needle}).fetchall()
    finally:
        con.close()
    meta = holder.meta
    return JSONResponse(
        SuggestOut(box=box, q=q, items=[Suggestion(name=n, slug=s) for n, s in rows]).model_dump(),
        headers=cache_headers(meta, f"suggest:{box}:{needle}"),
    )


@app.get("/api/flags/{flag:path}.png", include_in_schema=False)
def api_flag(flag: str):
    """Flag images as shown on speedrun.com leaderboards, fetched once and cached on disk."""
    if not FLAG_ID.match(flag):
        raise HTTPException(404)
    cached = settings.data_dir / "flags" / f"{flag}.png"
    if not cached.exists():
        try:
            r = httpx.get(FLAG_SOURCE + flag + ".png", timeout=15, follow_redirects=True)
        except httpx.HTTPError as e:
            raise HTTPException(502, "flag source unreachable") from e
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            raise HTTPException(404)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(r.content)
    return Response(cached.read_bytes(), media_type="image/png", headers={"Cache-Control": IMMUTABLE})


@app.get("/api/meta", response_model=MetaOut)
def api_meta():
    try:
        return meta_out(holder.meta)
    except NoDatabase as e:
        raise HTTPException(503, "no data published yet") from e


@app.get("/health", response_model=HealthOut)
def health():
    if not holder.ready:
        return JSONResponse(HealthOut(ok=False).model_dump(mode="json"), status_code=503)
    meta = holder.meta
    return HealthOut(ok=True, data_version=meta.data_version, scraped_at=meta.scraped_at, stale=meta.stale)


# -- SPA shell -------------------------------------------------------------------------------------------------------

if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


def _shell(request: Request) -> Response:
    index = DIST / "index.html"
    if not index.exists():
        return HTMLResponse(
            "<p>SpeedStats API is running. The web app is not built; run <code>npm run dev</code> and open "
            "<a href='http://localhost:5173'>localhost:5173</a>, or <code>npm run build</code>.</p>",
            status_code=200,
        )
    spec = parse_query(request.query_params.multi_items())
    title = describe(spec)
    page = index.read_text(encoding="utf-8")
    page = page.replace("__TITLE__", html.escape(f"SpeedStats - {title}" if request.query_params else "SpeedStats"))
    page = page.replace("__DESCRIPTION__", html.escape(f"{title} on SpeedStats, speedrun.com rankings by run value."))
    page = page.replace("__URL__", html.escape(f"{settings.public_url}/?{canonical_query(spec)}"))
    headers = {"Cache-Control": CACHE_CONTROL}
    if holder.ready:
        headers.update(cache_headers(holder.meta, f"shell:{canonical_query(spec)}"))
    return HTMLResponse(page, headers=headers)


@app.get("/", include_in_schema=False)
@app.get("/index.php", include_in_schema=False)
def shell(request: Request) -> Response:
    return _shell(request)


@app.get("/{path:path}", include_in_schema=False)
def shell_fallback(path: str, request: Request) -> Response:
    if path.startswith("api/"):
        raise HTTPException(404)
    file = DIST / path
    if file.is_file() and DIST in file.resolve().parents:
        return Response(file.read_bytes(), media_type=_media_type(file))
    return _shell(request)


def _media_type(file: Path) -> str:
    return {
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
        ".txt": "text/plain",
        ".json": "application/json",
        ".webmanifest": "application/manifest+json",
    }.get(file.suffix, "application/octet-stream")
