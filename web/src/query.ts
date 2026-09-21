/**
 * URL <-> query state. Mirrors speedstats/filters.py (keep the two in sync; both are covered by the same
 * test table).
 *
 * A query is `?s=<series>&g=<games>&p=<platforms>&u=<players>&l=<locations>&r=<request type>&m=<limit>`, every
 * box a comma-separated list of terms (`g=redball,redball2`); whitespace around a term is ignored. The long
 * names the original site used (`series`, `games`, `platforms`, `players`, `request-type`, `limit`; `locations`)
 * are accepted too, so its `games=Red+Ball%2C+Red+Ball+2` links keep working. A leading "!" on a term excludes
 * it (no speedrun.com name starts with "!"); terms cannot contain a comma.
 */

export const BOXES = ["series", "games", "platforms", "players", "locations"] as const;
export type BoxName = (typeof BOXES)[number];

/** The one-letter query keys links are written with. */
const SHORT_KEY: Record<BoxName, string> = { series: "s", games: "g", platforms: "p", players: "u", locations: "l" };
const REQUEST_TYPE_KEY = "r";
const LIMIT_KEY = "m";
/** Every accepted key (short and the original site's long form) -> what it means. */
const PARAM_ALIASES: Record<string, BoxName | "request-type" | "limit"> = {
  ...Object.fromEntries(BOXES.map((box) => [SHORT_KEY[box], box])),
  ...Object.fromEntries(BOXES.map((box) => [box, box])),
  [REQUEST_TYPE_KEY]: "request-type",
  "request-type": "request-type",
  [LIMIT_KEY]: "limit",
  limit: "limit",
};

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
const SEPARATOR = ",";

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

/** Every value is a comma-separated list; the pieces are trimmed and empty ones dropped. */
export function splitTerms(values: string[]): string[] {
  const out: string[] = [];
  for (const value of values) {
    for (const part of value.split(SEPARATOR)) {
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
  // group by meaning, in the order given, whichever spelling of the key was used
  const grouped: Record<string, string[]> = {};
  for (const [key, value] of params.entries()) {
    const meaning = PARAM_ALIASES[key];
    if (meaning) (grouped[meaning] ??= []).push(value);
  }
  const spec = emptySpec();
  for (const box of BOXES) spec.terms[box] = normalizeTerms(splitTerms(grouped[box] ?? []));
  const requestType = (grouped["request-type"]?.at(-1) ?? "").trim();
  spec.requestType = isRequestType(requestType) ? requestType : "pr";
  const rawLimit = (grouped.limit?.at(-1) ?? "").trim();
  if (rawLimit) {
    const n = Number.parseInt(rawLimit, 10);
    if (Number.isFinite(n)) spec.limit = Math.max(1, Math.min(MAX_LIMIT, n));
  }
  return spec;
}

/** Canonical params, as filters.to_params writes them: short keys, comma-joined terms in the order given. */
export function toSearchParams(spec: QuerySpec): URLSearchParams {
  const params = new URLSearchParams();
  for (const box of BOXES) {
    const terms = normalizeTerms(spec.terms[box]);
    if (terms.length) params.append(SHORT_KEY[box], terms.join(SEPARATOR));
  }
  params.append(REQUEST_TYPE_KEY, spec.requestType);
  if (spec.limit !== DEFAULT_LIMIT) params.append(LIMIT_KEY, String(spec.limit));
  return params;
}

export function toSearch(spec: QuerySpec): string {
  // "," and "!" are safe unescaped in a query string and read better in the address bar than %2C and %21
  return "?" + toSearchParams(spec).toString().replace(/%2C/g, ",").replace(/%21/g, "!");
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
