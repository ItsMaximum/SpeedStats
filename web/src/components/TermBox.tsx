import { useEffect, useId, useRef, useState, type KeyboardEvent, type ClipboardEvent } from "react";
import { fetchSuggestions, type Suggestion } from "../api";
import { normalizeTerms, type BoxName } from "../query";

interface Props {
  box: BoxName;
  label: string;
  terms: string[];
  onChange: (terms: string[]) => void;
  /** Text typed but not yet committed as a chip; the form includes it on submit. */
  text: string;
  onTextChange: (text: string) => void;
  onSubmit: () => void;
  placeholder?: string;
}

const DEBOUNCE_MS = 120;

/** Splits pasted text the way people write lists: newlines, or ", " like the old site. */
function splitPasted(text: string): string[] {
  return text.split(/\r?\n|, /).map((t) => t.trim()).filter(Boolean);
}

/**
 * A chips input: each term is a chip (exclusions, typed with a leading "-", are shown in red). Typing shows
 * autocomplete suggestions from the API; Enter or Tab commits the typed text (or the highlighted suggestion).
 * Commas do not split, because game names can contain them.
 */
export function TermBox({ box, label, terms, onChange, text, onTextChange: setText, onSubmit, placeholder }: Props) {
  const [items, setItems] = useState<Suggestion[]>([]);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  const exclude = text.startsWith("-");
  const needle = (exclude ? text.slice(1) : text).trim();

  useEffect(() => {
    if (!needle) {
      setItems([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetchSuggestions(box, needle, controller.signal)
        .then((found) => {
          setItems(found);
          setActive(-1);
        })
        .catch(() => setItems([]));
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [box, needle]);

  function commit(raw: string) {
    const next = normalizeTerms([...terms, raw]);
    if (next.length !== terms.length) onChange(next);
    setText("");
    setItems([]);
    setActive(-1);
  }

  function choose(item: Suggestion) {
    commit((exclude ? "-" : "") + item.name);
  }

  function remove(index: number) {
    onChange(terms.filter((_, i) => i !== index));
    inputRef.current?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" && items.length) {
      e.preventDefault();
      setActive((a) => (a + 1) % items.length);
    } else if (e.key === "ArrowUp" && items.length) {
      e.preventDefault();
      setActive((a) => (a <= 0 ? items.length - 1 : a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0 && items[active]) choose(items[active]);
      else if (text.trim()) commit(text);
      else onSubmit();
    } else if (e.key === "Tab" && text.trim()) {
      e.preventDefault();
      if (active >= 0 && items[active]) choose(items[active]);
      else commit(text);
    } else if (e.key === "Escape") {
      setItems([]);
      setActive(-1);
    } else if (e.key === "Backspace" && !text && terms.length) {
      e.preventDefault();
      const last = terms[terms.length - 1];
      onChange(terms.slice(0, -1));
      setText(last);
    }
  }

  function onPaste(e: ClipboardEvent<HTMLInputElement>) {
    const pasted = e.clipboardData.getData("text");
    const parts = splitPasted(pasted);
    if (parts.length > 1) {
      e.preventDefault();
      onChange(normalizeTerms([...terms, ...parts]));
      setText("");
    }
  }

  const showList = open && items.length > 0;

  return (
    <div className="termbox">
      <label className="termbox-label" htmlFor={`${listId}-input`}>
        {label}
      </label>
      <div className="chips" onClick={() => inputRef.current?.focus()}>
        {terms.map((term, i) => {
          const isExclude = term.startsWith("-");
          return (
            <span key={term} className={"chip" + (isExclude ? " chip-exclude" : "")} title={isExclude ? "excluded" : undefined}>
              {isExclude ? "− " + term.slice(1) : term}
              <button type="button" className="chip-remove" aria-label={`Remove ${term}`} onClick={() => remove(i)}>
                ×
              </button>
            </span>
          );
        })}
        <input
          id={`${listId}-input`}
          ref={inputRef}
          className="chips-input"
          value={text}
          placeholder={terms.length ? "" : placeholder}
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            // let a click on a suggestion land first
            setTimeout(() => setOpen(false), 150);
          }}
        />
      </div>
      {showList && (
        <ul className="suggestions" id={listId} role="listbox">
          {items.map((item, i) => (
            <li
              key={item.name + (item.slug ?? "")}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : undefined}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(item);
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span>
                {exclude && <span className="suggest-exclude">− </span>}
                {item.name}
              </span>
              {item.slug && <span className="suggest-slug">{item.slug}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
