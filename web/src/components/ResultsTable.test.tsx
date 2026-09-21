import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { QueryOut } from "../api";
import { ResultsTable } from "./ResultsTable";

const meta = {
  data_version: "t",
  scraped_at: "2026-09-18T00:00:00Z",
  published_at: "2026-09-18T00:00:00Z",
  row_count: 1,
  leaderboard_count: 1,
  player_count: 1,
  game_count: 1,
  stale: false,
};

const result: QueryOut = {
  request_type: "pr",
  title: "Player Rankings - all games",
  columns: ["Rank", "Player", "Points"],
  rows: [
    [1, "Alpha", 300.5],
    [2, "Beta", 200],
    [3, "Gamma", 100],
  ],
  truncated: false,
  warnings: [],
  players: {
    Alpha: { flag: "us", flag_name: "United States", color1: "#EE4444", color2: "#6666EE" },
    Beta: { flag: null, flag_name: null, color1: "#09B876", color2: null },
  },
  slugs: { Player: { Beta: "beta_srdc" } },
  term_info: {},
  meta,
};

describe("ResultsTable", () => {
  it("links player names, shows flags and speedrun.com colours", () => {
    render(<ResultsTable result={result} />);
    const alpha = screen.getByRole("link", { name: "Alpha" });
    expect(alpha).toHaveAttribute("href", "?u=Alpha&r=runs");
    expect(alpha).toHaveClass("username", "username-gradient");
    // a player with a distinct speedrun.com abbreviation links by it
    expect(screen.getByRole("link", { name: "Beta" })).toHaveAttribute("href", "?u=beta_srdc&r=runs");
    expect(screen.getByAltText("United States")).toHaveAttribute("src", "/api/flags/us.png");
    const beta = screen.getByRole("link", { name: "Beta" });
    expect(beta).not.toHaveClass("username-gradient");
    expect(beta).toHaveStyle({ color: "#09B876" });
    expect(screen.getByRole("link", { name: "Gamma" })).toHaveClass("username");
  });

  it("sorts by a column on header click and back", () => {
    render(<ResultsTable result={result} />);
    const names = () => screen.getAllByRole("link").map((a) => a.textContent);
    expect(names()).toEqual(["Alpha", "Beta", "Gamma"]);
    fireEvent.click(screen.getByText("Points"));
    expect(names()).toEqual(["Alpha", "Beta", "Gamma"]); // desc first for numeric columns
    fireEvent.click(screen.getByText("Points"));
    expect(names()).toEqual(["Gamma", "Beta", "Alpha"]);
    fireEvent.click(screen.getByText("Player"));
    fireEvent.click(screen.getByText("Player"));
    expect(names()).toEqual(["Alpha", "Beta", "Gamma"]);
  });

  it("formats points with exactly two decimals", () => {
    render(<ResultsTable result={{ ...result, rows: [[1, "Alpha", 300.5], [2, "Beta", 200], [3, "Gamma", 12.345]] }} />);
    expect(screen.getByText("300.50")).toBeInTheDocument();
    expect(screen.getByText("200.00")).toBeInTheDocument();
    expect(screen.getByText("12.35")).toBeInTheDocument();
  });
});
