/**
 * URL <-> query state. Mirrors speedstats/filters.py (keep the two in sync; both are covered by the same
 * test table).
 *
 * Legacy links (no `v` param) separate terms with ", ". New links (`v=2`) repeat the param once per term, so
 * names containing ", " work. A leading "-" on a term excludes it.
 */

export const BOXES = ["series", "games", "platforms", "players", "countries"] as const;
export type BoxName = (typeof BOXES)[number];

export const REQUEST_TYPES = ["pr", "runs", "records", "leaderboards", "games", "series", "dates"] as const;
export type RequestType = (typeof REQUEST_TYPES)[number];

export const REQUEST_TYPE_LABELS: Record<RequestType, string> = {
  pr: "Player Rankings",
  runs: "Runs by Player(s)",
  records: "Most Valuable Records",
  leaderboards: "Leaderboard Value",
  games: "Game Value",
  series: "Series Value",
  dates: "Date Value",
};

export const BOX_LABELS: Record<BoxName, string> = {
  series: "Series",
  games: "Games",
  platforms: "Platforms",
  players: "Players",
  countries: "Countries",
};

export const DEFAULT_LIMIT = 1000;
export const MAX_LIMIT = 5000;
const LEGACY_SEPARATOR = ", ";
const FORMAT_VERSION = "2";

/** Terms keep their leading "-" (exclusions) as typed. */
export interface QuerySpec {
  terms: Record<BoxName, string[]>;
  requestType: RequestType;
  limit: number;
}

interface BoxTerms {
  include: string[];
  exclude: string[];
}

export function emptySpec(): QuerySpec {
  return {
    terms: { series: [], games: [], platforms: [], players: [], countries: [] },
    requestType: "pr",
    limit: DEFAULT_LIMIT,
  };
}

function isRequestType(value: string): value is RequestType {
  return (REQUEST_TYPES as readonly string[]).includes(value);
}

export function splitTerms(values: string[], newFormat: boolean): string[] {
  const out: string[] = [];
  for (const value of values) {
    const parts = newFormat ? [value] : value.split(LEGACY_SEPARATOR);
    for (const part of parts) {
      const term = part.trim();
      if (term) out.push(term);
    }
  }
  return out;
}

/** Drop a bare "-" and case-insensitive duplicates, keeping first occurrence and original spelling. */
export function normalizeTerms(terms: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of terms) {
    let term = raw.trim();
    if (term.startsWith("-")) {
      const rest = term.slice(1).trim();
      if (!rest) continue;
      term = "-" + rest;
    }
    if (!term) continue;
    const key = term.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      out.push(term);
    }
  }
  return out;
}

export function splitSign(terms: string[]): BoxTerms {
  const include: string[] = [];
  const exclude: string[] = [];
  for (const term of normalizeTerms(terms)) {
    if (term.startsWith("-")) exclude.push(term.slice(1).trim());
    else include.push(term);
  }
  return { include, exclude };
}

export function parseQuery(search: string | URLSearchParams): QuerySpec {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const newFormat = params.getAll("v").includes(FORMAT_VERSION);
  const spec = emptySpec();
  for (const box of BOXES) {
    spec.terms[box] = normalizeTerms(splitTerms(params.getAll(box), newFormat));
  }
  const requestType = (params.getAll("request-type").at(-1) ?? "").trim();
  spec.requestType = isRequestType(requestType) ? requestType : "pr";
  const rawLimit = (params.getAll("limit").at(-1) ?? "").trim();
  if (rawLimit) {
    const n = Number.parseInt(rawLimit, 10);
    if (Number.isFinite(n)) spec.limit = Math.max(1, Math.min(MAX_LIMIT, n));
  }
  return spec;
}

/** Canonical v=2 params. Same ordering as filters.to_params so both sides produce identical URLs. */
export function toSearchParams(spec: QuerySpec): URLSearchParams {
  const params = new URLSearchParams();
  for (const box of BOXES) {
    const { include, exclude } = splitSign(spec.terms[box]);
    for (const term of include) params.append(box, term);
    for (const term of exclude) params.append(box, "-" + term);
  }
  params.append("request-type", spec.requestType);
  if (spec.limit !== DEFAULT_LIMIT) params.append("limit", String(spec.limit));
  params.append("v", FORMAT_VERSION);
  return params;
}

export function toSearch(spec: QuerySpec): string {
  return "?" + toSearchParams(spec).toString();
}

/** Link to a related query from a table cell (player -> their runs, game -> its rankings, ...). */
export function linkTo(box: BoxName, term: string, requestType: RequestType): string {
  const spec = emptySpec();
  spec.terms[box] = [term];
  spec.requestType = requestType;
  return toSearch(spec);
}
