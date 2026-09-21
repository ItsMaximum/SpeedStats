import { describe, expect, it } from "vitest";
import { canonicalize, emptySpec, linkTo, parseQuery, splitSign, toSearch, toSearchParams } from "./query";

// Shared with tests/test_filters.py - keep the two tables in sync.
const CASES: Array<[string, Record<string, unknown>]> = [
  // links from the original site: long keys, ", " separated
  ["series=&games=Red+Ball&platforms=&players=&request-type=pr", { games: { include: ["Red Ball"], exclude: [] }, requestType: "pr" }],
  ["games=Red+Ball%2C+Red+Ball+2&request-type=runs", { games: { include: ["Red Ball", "Red Ball 2"], exclude: [] }, requestType: "runs" }],
  ["players=Maximum%2C+!Someone&request-type=records", { players: { include: ["Maximum"], exclude: ["Someone"] }, requestType: "records" }],
  // the form the site writes: short keys, comma separated
  ["g=redball,redball2&r=pr", { games: { include: ["redball", "redball2"], exclude: [] }, requestType: "pr" }],
  ["s=a,+b+,c", { series: { include: ["a", "b", "c"], exclude: [] } }], // whitespace around a term is ignored
  ["g=!a,b,!c", { games: { include: ["b"], exclude: ["a", "c"] } }],
  ["g=a,,b,", { games: { include: ["a", "b"], exclude: [] } }], // empty pieces are dropped
  ["g=a&games=b", { games: { include: ["a", "b"], exclude: [] } }], // repeats merge, whichever spelling
  ["l=us,!ca", { locations: { include: ["us"], exclude: ["ca"] } }],
  ["u=Maximum&p=PC", { players: { include: ["Maximum"], exclude: [] }, platforms: { include: ["PC"], exclude: [] } }],
  ["r=runs", { requestType: "runs" }],
  ["m=50", { limit: 50 }],
  ["countries=us", { locations: { include: [], exclude: [] } }], // not a parameter (the original site had no such box)
  // defaults & oddities
  ["", { requestType: "pr", limit: 1000 }],
  ["g=!", { games: { include: [], exclude: [] } }],
  ["g=+Red+Ball+", { games: { include: ["Red Ball"], exclude: [] } }],
  ["g=Red+Ball,red+ball", { games: { include: ["Red Ball"], exclude: [] } }],
  ["limit=99999", { limit: 5000 }],
  ["limit=10", { limit: 10 }],
];

describe("parseQuery", () => {
  it.each(CASES)("%s", (search, expected) => {
    const spec = parseQuery(search);
    for (const [key, value] of Object.entries(expected)) {
      if (key === "requestType" || key === "limit") expect(spec[key]).toEqual(value);
      else expect(splitSign(spec.terms[key as keyof typeof spec.terms])).toEqual(value);
    }
  });

  it("falls back to pr for unknown request types", () => {
    expect(parseQuery("r=bogus").requestType).toBe("pr");
  });

  it("keeps the order of terms across spellings of a key", () => {
    expect(parseQuery("games=a&g=b&games=c").terms.games).toEqual(["a", "b", "c"]);
  });
});

describe("toSearchParams", () => {
  it("emits short keys with comma-joined terms and round-trips", () => {
    const spec = parseQuery("series=Red+Ball&games=!Red+Ball+5&locations=us&request-type=runs&limit=50");
    const params = toSearchParams(spec);
    expect([...params.entries()]).toEqual([
      ["s", "Red Ball"],
      ["g", "!Red Ball 5"],
      ["l", "us"],
      ["r", "runs"],
      ["m", "50"],
    ]);
    expect(parseQuery(params)).toEqual(spec);
    expect(toSearch(emptySpec())).toBe("?r=pr"); // empty boxes are left out, the default limit too
  });

  it("re-encodes a legacy link in the short form", () => {
    const search = toSearch(parseQuery("games=Red+Ball%2C+Red+Ball+2&request-type=pr"));
    expect(search).toBe("?g=Red+Ball,Red+Ball+2&r=pr");
  });

  it("builds cell links", () => {
    expect(linkTo("players", "Maximum", "runs")).toBe("?u=Maximum&r=runs");
  });
});

describe("term order", () => {
  it("keeps includes and exclusions in the order given", () => {
    const spec = parseQuery("g=!A,B&g=!C,b");
    expect(spec.terms.games).toEqual(["!A", "B", "!C"]);
    expect(toSearch(spec)).toBe("?g=!A,B,!C&r=pr");
  });
});

describe("canonicalize", () => {
  const info = {
    games: {
      "The Fancy Pants Adventures: World 1": { name: "The Fancy Pants Adventures: World 1", slug: "fpa1" },
      "Red Ball 4": { name: "Red Ball 4", slug: "redball4" },
      Georgia: { name: "Georgia", slug: null }, // matched more than one thing
      FPA2: { name: "The Fancy Pants Adventures: World 2", slug: "fpa2" },
    },
    locations: { europe: { name: "Europe", slug: "EU" } },
  };

  it("swaps resolved names for their abbreviations, keeping the exclusion sign", () => {
    const spec = emptySpec();
    spec.terms.games = ["The Fancy Pants Adventures: World 1", "!Red Ball 4"];
    spec.terms.locations = ["europe"];
    const out = canonicalize(spec, info);
    expect(out.terms.games).toEqual(["fpa1", "!redball4"]);
    expect(out.terms.locations).toEqual(["EU"]);
    expect(out.requestType).toBe(spec.requestType);
  });

  it("leaves unknown and ambiguous terms, and terms that already are the abbreviation, as they are", () => {
    const spec = emptySpec();
    spec.terms.games = ["Georgia", "Atlantis", "FPA2"];
    const out = canonicalize(spec, info);
    expect(out.terms.games).toEqual(["Georgia", "Atlantis", "FPA2"]);
    expect(toSearch(out)).toBe(toSearch(spec));
  });

  it("keeps the exclusion sign readable in the address bar", () => {
    const spec = emptySpec();
    spec.terms.games = ["!Red Ball 4"];
    expect(toSearch(spec)).toBe("?g=!Red+Ball+4&r=pr");
    expect(parseQuery(toSearch(spec)).terms.games).toEqual(["!Red Ball 4"]);
  });

  it("merges a typed name and its abbreviation into one term", () => {
    const spec = emptySpec();
    spec.terms.games = ["Red Ball 4", "redball4"];
    expect(canonicalize(spec, info).terms.games).toEqual(["redball4"]);
  });
});
