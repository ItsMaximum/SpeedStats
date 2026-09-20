import { useEffect, useId, useRef, useState, type ReactNode } from "react";

interface Props {
  label: string;
  /** Menu items: `<a>` or `<button>` elements; the menu closes after any of them is clicked. */
  children: ReactNode;
}

/** A button that opens a small dropdown menu below it. Closes on a click outside, on Escape or after a choice. */
export function MenuButton({ label, children }: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: globalThis.MouseEvent) {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="menu" ref={root}>
      <button
        type="button"
        className="menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((o) => !o)}
      >
        {label}
        <span className="menu-caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div className="menu-list" id={menuId} role="menu" onClick={() => setOpen(false)}>
          {children}
        </div>
      )}
    </div>
  );
}
