import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fetchSuggestions } from "../api";
import { TermBox } from "./TermBox";

vi.mock("../api", () => ({ fetchSuggestions: vi.fn().mockResolvedValue([]) }));

function setup(terms: string[] = [], text = "", names?: Record<string, string>, invalid?: string[]) {
  const onChange = vi.fn();
  const onTextChange = vi.fn();
  const onSubmit = vi.fn();
  render(
    <TermBox
      box="games"
      label="Games"
      terms={terms}
      onChange={onChange}
      text={text}
      onTextChange={onTextChange}
      onSubmit={onSubmit}
      names={names}
      invalid={invalid}
    />,
  );
  return { onChange, onTextChange, onSubmit, input: screen.getByRole("combobox") };
}

describe("TermBox", () => {
  it("Enter on typed text submits the form instead of making a chip", () => {
    const { onChange, onTextChange, onSubmit, input } = setup([], "Red Ball");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled(); // the chip appears once the query resolved it
    expect(onTextChange).not.toHaveBeenCalled(); // the text stays in the box meanwhile
  });

  it("marks a term the query could not match as an invalid chip", () => {
    setup(["fpa1", "nosuchgame"], "", { fpa1: "The Fancy Pants Adventures: World 1" }, ["nosuchgame"]);
    expect(screen.getByText("nosuchgame")).toHaveClass("chip-invalid");
    expect(screen.getByText("The Fancy Pants Adventures: World 1")).not.toHaveClass("chip-invalid");
  });

  it("submits the form on Enter when nothing is typed", () => {
    const { onSubmit, input } = setup(["Red Ball"], "");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalled();
  });

  it("shows exclusions as red chips and removes them", () => {
    const { onChange } = setup(["Red Ball", "!Red Ball 5"]);
    expect(screen.getByText("!Red Ball 5")).toHaveClass("chip-exclude");
    fireEvent.click(screen.getByLabelText("Remove !Red Ball 5"));
    expect(onChange).toHaveBeenCalledWith(["Red Ball"]);
  });

  it("backspace on an empty input pulls the last chip back into the input", () => {
    const { onChange, onTextChange, input } = setup(["A", "B"], "");
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(onChange).toHaveBeenCalledWith(["A"]);
    expect(onTextChange).toHaveBeenCalledWith("B");
  });

  it("splits pasted lists into chips", () => {
    const { onChange, input } = setup([]);
    fireEvent.paste(input, { clipboardData: { getData: () => "Red Ball, Red Ball 2\nRed Ball 3" } });
    expect(onChange).toHaveBeenCalledWith(["Red Ball", "Red Ball 2", "Red Ball 3"]);
  });

  it("shows the full name on a chip while the term stays abbreviated", () => {
    const { onChange } = setup(["fpa1", "!EU", "unknown"], "", { fpa1: "The Fancy Pants Adventures: World 1", eu: "Europe" });
    expect(screen.getByText("The Fancy Pants Adventures: World 1")).toHaveClass("chip");
    expect(screen.getByText("!Europe")).toHaveClass("chip-exclude");
    expect(screen.getByText("unknown")).toHaveClass("chip"); // nothing known yet: shown as typed
    expect(screen.queryByText("fpa1")).not.toBeInTheDocument();
    expect(screen.getByText("!Europe")).not.toHaveAttribute("title");
    fireEvent.click(screen.getByLabelText("Remove !Europe"));
    expect(onChange).toHaveBeenCalledWith(["fpa1", "unknown"]); // the URL form is what is removed
  });

  it("a picked suggestion commits its abbreviation and shows its name straight away", async () => {
    vi.mocked(fetchSuggestions).mockResolvedValue([{ name: "The Fancy Pants Adventures: World 1", slug: "fpa1" }]);
    const onChange = vi.fn();
    function Harness() {
      const [terms, setTerms] = useState<string[]>([]);
      const [text, setText] = useState("");
      return (
        <TermBox
          box="games"
          label="Games"
          terms={terms}
          onChange={(t) => {
            onChange(t);
            setTerms(t);
          }}
          text={text}
          onTextChange={setText}
          onSubmit={() => undefined}
        />
      );
    }
    render(<Harness />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "world" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: /World 1/ }));
    expect(onChange).toHaveBeenCalledWith(["fpa1"]); // the URL gets the abbreviation
    expect(screen.getByText("The Fancy Pants Adventures: World 1")).toHaveClass("chip"); // the chip, the name
  });

  it("ignores case-insensitive duplicates", () => {
    const { onChange, input } = setup(["Red Ball"], "red ball");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).not.toHaveBeenCalled();
  });
});
