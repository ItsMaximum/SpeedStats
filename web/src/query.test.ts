import { describe, expect, it } from "vitest";
import { linkTo, parseQuery, splitSign, toSearch, toSearchParams } from "./query";

// Shared with tests/test_filters.py - keep the two tables in sync.
const CASES: Array<[string, Record<string, unknown>]> = [
  ["series=&games=Red+Ball&platforms=&players=&request-type=pr", { games: { include: ["Red Ball"], exclude: [] }, requestType: "pr" }],
  ["games=Red+Ball%2C+Red+Ball+2&request-type=runs", { games: { include: ["Red Ball", "Red Ball 2"], exclude: [] }, requestType: "runs" }],
  ["players=Maximum%2C+-Someone&request-type=records", { players: { include: ["Maximum"], exclude: ["Someone"] }, requestType: "records" }],
  ["games=A,B", { games: { include: ["A,B"], exclude: [] } }],
  ["games=Sonic%2C+Redux&v=2", { games: { include: ["Sonic, Redux"], exclude: [] } }],
  [
    "series=Red+Ball&games=-Red+Ball+5&v=2&request-type=pr",
    { series: { include: ["Red Ball"], exclude: [] }, games: { include: [], exclude: ["Red Ball 5"] } },
  ],
  ["games=A&games=B&games=-C&v=2", { games: { include: ["A", "B"], exclude: ["C"] } }],
  ["countries=us&countries=-ca&v=2", { countries: { include: ["us"], exclude: ["ca"] } }],
  ["", { requestType: "pr", limit: 1000 }],
  ["games=-&v=2", { games: { include: [], exclude: [] } }],
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
    const spec = parseQuery("series=Red+Ball&games=-Red+Ball+5&countries=us&request-type=runs&limit=50&v=2");
    const params = toSearchParams(spec);
    expect([...params.entries()]).toEqual([
      ["series", "Red Ball"],
      ["games", "-Red Ball 5"],
      ["countries", "us"],
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
