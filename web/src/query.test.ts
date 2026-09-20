import { describe, expect, it } from "vitest";
import { canonicalize, emptySpec, linkTo, parseQuery, splitSign, toSearch, toSearchParams } from "./query";

// Shared with tests/test_filters.py - keep the two tables in sync.
const CASES: Array<[string, Record<string, unknown>]> = [
  ["series=&games=Red+Ball&platforms=&players=&request-type=pr", { games: { include: ["Red Ball"], exclude: [] }, requestType: "pr" }],
  ["games=Red+Ball%2C+Red+Ball+2&request-type=runs", { games: { include: ["Red Ball", "Red Ball 2"], exclude: [] }, requestType: "runs" }],
  ["players=Maximum%2C+!Someone&request-type=records", { players: { include: ["Maximum"], exclude: ["Someone"] }, requestType: "records" }],
  ["games=A,B", { games: { include: ["A,B"], exclude: [] } }],
  ["games=Sonic%2C+Redux&v=2", { games: { include: ["Sonic, Redux"], exclude: [] } }],
  [
    "series=Red+Ball&games=!Red+Ball+5&v=2&request-type=pr",
    { series: { include: ["Red Ball"], exclude: [] }, games: { include: [], exclude: ["Red Ball 5"] } },
  ],
  ["games=A&games=B&games=!C&v=2", { games: { include: ["A", "B"], exclude: ["C"] } }],
  ["locations=us&locations=!ca&v=2", { locations: { include: ["us"], exclude: ["ca"] } }],
  ["countries=England&locations=!us&v=2", { locations: { include: ["England"], exclude: ["us"] } }],
  ["", { requestType: "pr", limit: 1000 }],
  ["games=!&v=2", { games: { include: [], exclude: [] } }],
  ["games=+Red+Ball+&v=2", { games: { include: ["Red Ball"], exclude: [] } }],
  ["games=Red+Ball&games=red+ball&v=2", { games: { include: ["Red Ball"], exclude: [] } }],
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
    expect(parseQuery("request-type=bogus").requestType).toBe("pr");
  });
});

describe("toSearchParams", () => {
  it("emits the canonical v=2 form and round-trips", () => {
    const spec = parseQuery("series=Red+Ball&games=!Red+Ball+5&locations=us&request-type=runs&limit=50&v=2");
    const params = toSearchParams(spec);
    expect([...params.entries()]).toEqual([
      ["series", "Red Ball"],
      ["games", "!Red Ball 5"],
      ["locations", "us"],
      ["request-type", "runs"],
      ["limit", "50"],
      ["v", "2"],
    ]);
    expect(parseQuery(params)).toEqual(spec);
  });

  it("re-encodes a legacy link so commas survive", () => {
    const search = toSearch(parseQuery("games=Red+Ball%2C+Red+Ball+2&request-type=pr"));
    expect(search).toBe("?games=Red+Ball&games=Red+Ball+2&request-type=pr&v=2");
  });

  it("builds cell links", () => {
    expect(linkTo("players", "Max, Imum", "runs")).toBe("?players=Max%2C+Imum&request-type=runs&v=2");
  });
});

describe("term order", () => {
  it("keeps includes and exclusions in the order given", () => {
    const spec = parseQuery("games=!A&games=B&games=!C&games=b&v=2");
    expect(spec.terms.games).toEqual(["!A", "B", "!C"]);
    expect(toSearch(spec)).toBe("?games=!A&games=B&games=!C&request-type=pr&v=2");
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
    expect(toSearch(spec)).toBe("?games=!Red+Ball+4&request-type=pr&v=2");
    expect(parseQuery(toSearch(spec)).terms.games).toEqual(["!Red Ball 4"]);
  });

  it("merges a typed name and its abbreviation into one term", () => {
    const spec = emptySpec();
    spec.terms.games = ["Red Ball 4", "redball4"];
    expect(canonicalize(spec, info).terms.games).toEqual(["redball4"]);
  });
});
