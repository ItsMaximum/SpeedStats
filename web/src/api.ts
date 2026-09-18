import type { components } from "./api-types";
import type { BoxName, QuerySpec } from "./query";
import { toSearchParams } from "./query";

export type QueryOut = components["schemas"]["QueryOut"];
export type MetaOut = components["schemas"]["MetaOut"];
export type SuggestOut = components["schemas"]["SuggestOut"];
export type Suggestion = components["schemas"]["Suggestion"];
export type Cell = QueryOut["rows"][number][number];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, res.status === 503 ? "No data has been published yet." : String(detail));
  }
  return (await res.json()) as T;
}

export function queryUrl(spec: QuerySpec, format?: "csv" | "json"): string {
  const params = toSearchParams(spec);
  if (format) params.append("format", format);
  return "/api/query?" + params.toString();
}

export function fetchQuery(spec: QuerySpec, signal?: AbortSignal): Promise<QueryOut> {
  return getJson<QueryOut>(queryUrl(spec), signal);
}

export function fetchMeta(signal?: AbortSignal): Promise<MetaOut> {
  return getJson<MetaOut>("/api/meta", signal);
}

export function fetchSuggestions(box: BoxName, q: string, signal?: AbortSignal): Promise<Suggestion[]> {
  const params = new URLSearchParams({ box, q });
  return getJson<SuggestOut>("/api/suggest?" + params.toString(), signal).then((r) => r.items);
}
