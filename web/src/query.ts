/**
 * URL <-> query state. Mirrors speedstats/filters.py (keep the two in sync; both are covered by the same
 * test table).
 *
 * Legacy links (no `v` param) separate terms with ", ". New links (`v=2`) repeat the param once per term, so
 * names containing ", " work. A leading "!" on a term excludes it (no speedrun.com name starts with "!").
 */

export const BOXES = ["series", "games", "platforms", "players", "locations"] as const;
export type BoxName = (typeof BOXES)[number];
const BOX_ALIASES: Record<string, BoxName> = { countries: "locations" };

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
  locations: "Locations",
};

export const DEFAULT_LIMIT = 1000;
export const MAX_LIMIT = 5000;
const LEGACY_SEPARATOR = ", ";
const FORMAT_VERSION = "2";

/** Terms keep their leading "!" (exclusions) as typed. */
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
    terms: { series: [], games: [], platforms: [], players: [], locations: [] },
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

/** Drop a bare "!" and case-insensitive duplicates, keeping first occurrence and original spelling. */
export function normalizeTerms(terms: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of terms) {
    let term = raw.trim();
    if (term.startsWith("!")) {
      const rest = term.slice(1).trim();
      if (!rest) continue;
      term = "!" + rest;
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
    if (term.startsWith("!")) exclude.push(term.slice(1).trim());
    else include.push(term);
  }
  return { include, exclude };
}

export function parseQuery(search: string | URLSearchParams): QuerySpec {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const newFormat = params.getAll("v").includes(FORMAT_VERSION);
  const spec = emptySpec();
  for (const box of BOXES) {
    const aliases = Object.entries(BOX_ALIASES).filter(([, target]) => target === box).map(([alias]) => alias);
    const values = [...params.getAll(box), ...aliases.flatMap((alias) => params.getAll(alias))];
    spec.terms[box] = normalizeTerms(splitTerms(values, newFormat));
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

/** Canonical v=2 params: terms in the order given, as filters.to_params does, so both sides agree. */
export function toSearchParams(spec: QuerySpec): URLSearchParams {
  const params = new URLSearchParams();
  for (const box of BOXES) {
    for (const term of normalizeTerms(spec.terms[box])) params.append(box, term);
  }
  params.append("request-type", spec.requestType);
  if (spec.limit !== DEFAULT_LIMIT) params.append("limit", String(spec.limit));
  params.append("v", FORMAT_VERSION);
  return params;
}

export function toSearch(spec: QuerySpec): string {
  // "!" is safe unescaped in a query string and reads better in the address bar than %21
  return "?" + toSearchParams(spec).toString().replace(/%21/g, "!");
}

/** What the API said each term resolved to: box -> term as given -> name and (unambiguous) abbreviation. */
export type TermInfo = Record<string, Record<string, { name: string; slug?: string | null }>>;

/** Split a term into its exclusion sign and the bare text. */
export function splitTerm(term: string): { sign: "" | "!"; bare: string } {
  return term.startsWith("!") ? { sign: "!", bare: term.slice(1) } : { sign: "", bare: term };
}

/**
 * The same query with every term replaced by its abbreviation, so URLs stay short however a term was
 * entered. Terms the API did not resolve, or that matched more than one thing (no slug), stay as typed.
 */
export function canonicalize(spec: QuerySpec, info: TermInfo): QuerySpec {
  const terms = { ...spec.terms };
  for (const box of BOXES) {
    terms[box] = normalizeTerms(
      spec.terms[box].map((term) => {
        const { sign, bare } = splitTerm(term);
        const slug = info[box]?.[bare]?.slug;
        return slug && slug.toLowerCase() !== bare.toLowerCase() ? sign + slug : term;
      }),
    );
  }
  return { ...spec, terms };
}

/** Link to a related query from a table cell (player -> their runs, game -> its rankings, ...). */
export function linkTo(box: BoxName, term: string, requestType: RequestType): string {
  const spec = emptySpec();
  spec.terms[box] = [term];
  spec.requestType = requestType;
  return toSearch(spec);
}
