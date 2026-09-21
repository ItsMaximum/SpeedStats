import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { emptySpec, toSearch, type QuerySpec } from "../query";
import { QueryForm } from "./QueryForm";

function setup(spec: QuerySpec = emptySpec(), loading = false) {
  const onSubmit = vi.fn();
  const view = render(<QueryForm spec={spec} onSubmit={onSubmit} meta={null} loading={loading} />);
  const button = () => screen.getByRole("button", { name: loading ? "Loading" : "Submit" });
  const box = (label: string) => screen.getByRole("combobox", { name: label });
  return { onSubmit, button, box, rerender: view.rerender };
}

describe("QueryForm submit button", () => {
  it("is disabled when the form matches the query on screen", () => {
    const { button, onSubmit } = setup();
    expect(button()).toBeDisabled();
    fireEvent.submit(button().closest("form")!);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("enables once text is typed and submits it as a term", () => {
    const { button, box, onSubmit } = setup();
    fireEvent.change(box("Games"), { target: { value: "Red Ball" } });
    expect(button()).toBeEnabled();
    fireEvent.click(button());
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0].terms.games).toEqual(["Red Ball"]);
  });

  it("keeps showing the typed text while the query loads and makes it a chip once it is done", () => {
    const { box, onSubmit, rerender } = setup();
    fireEvent.change(box("Games"), { target: { value: "fpa3" } });
    fireEvent.keyDown(box("Games"), { key: "Enter" });
    const submitted = onSubmit.mock.calls[0][0] as QuerySpec;
    expect(submitted.terms.games).toEqual(["fpa3"]);
    // the URL changed but the query is in flight: no chip yet, text still in the box
    const before = toSearch(emptySpec());
    rerender(<QueryForm spec={submitted} onSubmit={onSubmit} meta={null} loading resolvedFor={before} />);
    expect(screen.queryByText("fpa3", { selector: ".chip" })).not.toBeInTheDocument();
    expect(box("Games")).toHaveValue("fpa3");
    // the result is back with the name: the chip appears with it, the box is empty
    const names = { series: {}, games: { fpa3: "The Fancy Pants Adventures: World 3" }, platforms: {}, players: {}, locations: {} };
    rerender(
      <QueryForm spec={submitted} onSubmit={onSubmit} meta={null} names={names} resolvedFor={toSearch(submitted)} />,
    );
    expect(screen.getByText("The Fancy Pants Adventures: World 3")).toHaveClass("chip");
    expect(box("Games")).toHaveValue("");
  });

  it("counts text that is typed but not yet a chip as a change", () => {
    const { button, box } = setup();
    fireEvent.change(box("Players"), { target: { value: "Maximum" } });
    expect(button()).toBeEnabled();
    fireEvent.change(box("Players"), { target: { value: "   " } });
    expect(button()).toBeDisabled();
  });

  it("enables when the request type changes and disables again when it is changed back", () => {
    const { button } = setup();
    const select = screen.getByLabelText("Request Type");
    fireEvent.change(select, { target: { value: "runs" } });
    expect(button()).toBeEnabled();
    fireEvent.change(select, { target: { value: "pr" } });
    expect(button()).toBeDisabled();
  });

  it("stays disabled for edits that do not change the query, like a duplicate chip", () => {
    const spec = emptySpec();
    spec.terms.games = ["Red Ball"];
    const { button, box } = setup(spec);
    fireEvent.change(box("Games"), { target: { value: "red ball" } });
    fireEvent.keyDown(box("Games"), { key: "Enter" });
    expect(button()).toBeDisabled();
  });

  it("disables again after the submitted query becomes the one on screen", () => {
    const { button, box, onSubmit, rerender } = setup();
    fireEvent.change(box("Games"), { target: { value: "Red Ball" } });
    fireEvent.keyDown(box("Games"), { key: "Enter" });
    fireEvent.click(button());
    const submitted = onSubmit.mock.calls[0][0] as QuerySpec;
    rerender(<QueryForm spec={submitted} onSubmit={onSubmit} meta={null} />);
    expect(button()).toBeDisabled();
  });

  it("removing a chip makes the form dirty and Enter in a box submits it", () => {
    const spec = emptySpec();
    spec.terms.games = ["Red Ball", "Red Ball 2"];
    const { button, box, onSubmit } = setup(spec);
    fireEvent.click(screen.getByLabelText("Remove Red Ball 2"));
    expect(button()).toBeEnabled();
    fireEvent.keyDown(box("Games"), { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0].terms.games).toEqual(["Red Ball"]);
  });

  it("reports whether there is anything to clear, and clears every box when asked", () => {
    const onHasValues = vi.fn();
    const onSubmit = vi.fn();
    const spec = emptySpec();
    spec.terms.games = ["Red Ball"];
    const { rerender } = render(<QueryForm spec={spec} onSubmit={onSubmit} meta={null} onHasValues={onHasValues} />);
    expect(onHasValues).toHaveBeenLastCalledWith(true);
    fireEvent.change(screen.getByRole("combobox", { name: "Players" }), { target: { value: "Max" } });
    rerender(<QueryForm spec={spec} onSubmit={onSubmit} meta={null} onHasValues={onHasValues} clearCount={1} />);
    expect(screen.queryByText("Red Ball")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Players" })).toHaveValue("");
    expect(onHasValues).toHaveBeenLastCalledWith(false);
    expect(screen.getByRole("button", { name: "Submit" })).toBeEnabled(); // the empty form differs from the query
  });

  it("does not submit a clean form on Enter even with the box focused", () => {
    const spec = emptySpec();
    spec.terms.games = ["Red Ball"];
    const { box, onSubmit } = setup(spec);
    fireEvent.keyDown(box("Games"), { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
