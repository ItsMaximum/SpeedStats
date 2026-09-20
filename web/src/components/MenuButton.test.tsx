import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MenuButton } from "./MenuButton";

function setup() {
  const onPick = vi.fn();
  render(
    <div>
      <MenuButton label="Export">
        <button type="button" role="menuitem" onClick={onPick}>
          CSV
        </button>
      </MenuButton>
      <p>outside</p>
    </div>,
  );
  return { onPick, button: screen.getByRole("button", { name: "Export" }) };
}

describe("MenuButton", () => {
  it("is closed until clicked, then shows its items", () => {
    const { button } = setup();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(button);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "CSV" })).toBeVisible();
    expect(button).toHaveAttribute("aria-expanded", "true");
  });

  it("runs the chosen item and closes", () => {
    const { button, onPick } = setup();
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("menuitem", { name: "CSV" }));
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes on a click outside and on Escape, and toggles on its own button", () => {
    const { button } = setup();
    fireEvent.click(button);
    fireEvent.mouseDown(screen.getByText("outside"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(button);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(button);
    fireEvent.click(button);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
