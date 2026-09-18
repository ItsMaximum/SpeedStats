import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TermBox } from "./TermBox";

function setup(terms: string[] = [], text = "") {
  const onChange = vi.fn();
  const onTextChange = vi.fn();
  const onSubmit = vi.fn();
  render(
    <TermBox box="games" label="Games" terms={terms} onChange={onChange} text={text} onTextChange={onTextChange} onSubmit={onSubmit} />,
  );
  return { onChange, onTextChange, onSubmit, input: screen.getByRole("combobox") };
}

describe("TermBox", () => {
  it("commits typed text as a chip on Enter and clears the input", () => {
    const { onChange, onTextChange, input } = setup([], "Red Ball");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(["Red Ball"]);
    expect(onTextChange).toHaveBeenCalledWith("");
  });

  it("submits the form on Enter when nothing is typed", () => {
    const { onSubmit, input } = setup(["Red Ball"], "");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalled();
  });

  it("shows exclusions as red chips and removes them", () => {
    const { onChange } = setup(["Red Ball", "-Red Ball 5"]);
    expect(screen.getByText("− Red Ball 5")).toHaveClass("chip-exclude");
    fireEvent.click(screen.getByLabelText("Remove -Red Ball 5"));
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

  it("ignores case-insensitive duplicates", () => {
    const { onChange, input } = setup(["Red Ball"], "red ball");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).not.toHaveBeenCalled();
  });
});
