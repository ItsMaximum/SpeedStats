import { useEffect, useState, type FormEvent } from "react";
import {
  BOXES,
  BOX_LABELS,
  REQUEST_TYPES,
  emptySpec,
  REQUEST_TYPE_LABELS,
  normalizeTerms,
  toSearch,
  type BoxName,
  type QuerySpec,
  type RequestType,
} from "../query";
import type { MetaOut } from "../api";
import { LastUpdated } from "./LastUpdated";
import { Spinner } from "./Spinner";
import { TermBox } from "./TermBox";

interface Props {
  spec: QuerySpec;
  onSubmit: (spec: QuerySpec) => void;
  meta: MetaOut | null;
  /** A query is in flight; the submit button shows a spinner. */
  loading?: boolean;
  /** Full names for the chips (box -> lowercased term -> name); terms without one show as typed. */
  names?: Record<BoxName, Record<string, string>>;
  /** Terms of the query on screen that matched nothing (box -> lowercased terms); their chips turn yellow. */
  invalid?: Record<BoxName, string[]>;
  /** The URL (toSearch form) that the names and results on screen answer; the form only adopts `spec` once
   *  this catches up with it, so typed text becomes a chip together with its full name. */
  resolvedFor?: string;
  /** Bump to clear every box (chips and typed text); the request type is kept. */
  clearCount?: number;
  /** Reports whether any box holds a chip or typed text, for enabling the Clear button. */
  onHasValues?: (hasValues: boolean) => void;
}

const PLACEHOLDERS: Record<BoxName, string> = {
  series: "e.g. Red Ball or redball",
  games: "e.g. Red Ball 4 or redball4",
  platforms: "e.g. Web, PC, SNES",
  players: "e.g. Maximum",
  locations: "e.g. United States, Colorado, Europe",
};

/** The boxes in the two-column grid; locations sits in the bottom row next to the request type. */
const GRID_BOXES = BOXES.filter((box) => box !== "locations");

const NO_TEXT: Record<BoxName, string> = { series: "", games: "", platforms: "", players: "", locations: "" };

export function QueryForm({
  spec,
  onSubmit,
  meta,
  loading = false,
  names,
  invalid,
  resolvedFor,
  clearCount = 0,
  onHasValues,
}: Props) {
  const [draft, setDraft] = useState<QuerySpec>(spec);
  const [pending, setPending] = useState<Record<BoxName, string>>(NO_TEXT);

  // A navigation (back/forward, cell link) replaces whatever was being typed. Until the query for `spec` has
  // answered, the form keeps showing what was submitted (typed text stays in its box), so a chip only appears
  // once its full name is known.
  useEffect(() => {
    if (resolvedFor !== undefined && resolvedFor !== toSearch(spec)) return;
    setDraft(spec);
    setPending(NO_TEXT);
  }, [spec, resolvedFor]);

  useEffect(() => {
    if (!clearCount) return;
    setDraft((d) => ({ ...d, terms: emptySpec().terms }));
    setPending(NO_TEXT);
  }, [clearCount]);

  const hasValues = BOXES.some((box) => draft.terms[box].length > 0 || pending[box].trim() !== "");
  useEffect(() => onHasValues?.(hasValues), [hasValues, onHasValues]);

  function setTerms(box: BoxName, terms: string[]) {
    setDraft((d) => ({ ...d, terms: { ...d.terms, [box]: terms } }));
  }

  function setText(box: BoxName, text: string) {
    setPending((p) => ({ ...p, [box]: text }));
  }

  /** The spec the form would submit now: the chips plus whatever is still typed, so Enter is never required. */
  function proposed(): QuerySpec {
    const terms = { ...draft.terms };
    for (const box of BOXES) {
      if (pending[box].trim()) terms[box] = normalizeTerms([...terms[box], pending[box]]);
    }
    return { ...draft, terms };
  }

  // Dirty when submitting would run a different query than the one on screen. Compared through the canonical
  // URL so that case, order and duplicates count the same way the query itself treats them.
  const dirty = toSearch(proposed()) !== toSearch(spec);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!dirty) return;
    onSubmit(proposed()); // typed text stays in its box until the result is back (see the effect above)
  }

  const termBox = (box: BoxName) => (
    <TermBox
      key={box}
      box={box}
      label={BOX_LABELS[box]}
      terms={draft.terms[box]}
      onChange={(terms) => setTerms(box, terms)}
      text={pending[box]}
      onTextChange={(text) => setText(box, text)}
      onSubmit={() => submit()}
      placeholder={PLACEHOLDERS[box]}
      names={names?.[box]}
      invalid={invalid?.[box]}
    />
  );

  return (
    <form className="query-form" onSubmit={submit}>
      <p className="intro">
        Type a name or its speedrun.com abbreviation and press Enter to add it. Prefix a term with <code>!</code> to
        exclude it. Series, games and platforms combine; leave them empty for all games.
      </p>

      <div className="grid-container">{GRID_BOXES.map((box) => termBox(box))}</div>

      {/* Locations shares its row with the request type and the submit button on a desktop */}
      <div className="bottom-row">
        {termBox("locations")}
        <div className="request">
          <div className="field">
            <label className="termbox-label" htmlFor="request-type">
              Request Type
            </label>
            <select
              id="request-type"
              name="request-type"
              value={draft.requestType}
              onChange={(e) => setDraft((d) => ({ ...d, requestType: e.target.value as RequestType }))}
            >
              {REQUEST_TYPES.map((rt) => (
                <option key={rt} value={rt}>
                  {REQUEST_TYPE_LABELS[rt]}
                </option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            className="submit"
            disabled={!dirty}
            aria-busy={loading}
            aria-label={loading ? "Loading" : undefined}
          >
            {loading ? <Spinner /> : "Submit"}
          </button>
        </div>
      </div>

      <div className="form-footer">
        <p className="credits">
          Created by <a href="https://www.speedrun.com/users/Maximum">Maximum</a>
          <span> &bull; </span>
          <a href="https://www.desmos.com/calculator/uvredthnmv">Formula</a>
          <span> &bull; </span>
          <a href="https://github.com/ItsMaximum/SpeedStats">Source Code</a>
        </p>
        {meta && <LastUpdated meta={meta} />}
      </div>
    </form>
  );
}
