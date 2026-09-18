import { useEffect, useState, type FormEvent } from "react";
import {
  BOXES,
  BOX_LABELS,
  REQUEST_TYPES,
  REQUEST_TYPE_LABELS,
  normalizeTerms,
  type BoxName,
  type QuerySpec,
  type RequestType,
} from "../query";
import { TermBox } from "./TermBox";

interface Props {
  spec: QuerySpec;
  onSubmit: (spec: QuerySpec) => void;
}

const PLACEHOLDERS: Record<BoxName, string> = {
  series: "e.g. Red Ball or redball",
  games: "e.g. Red Ball 4 or redball4",
  platforms: "e.g. Web, PC, SNES",
  players: "e.g. Maximum",
  countries: "e.g. United States or us",
};

const NO_TEXT: Record<BoxName, string> = { series: "", games: "", platforms: "", players: "", countries: "" };

export function QueryForm({ spec, onSubmit }: Props) {
  const [draft, setDraft] = useState<QuerySpec>(spec);
  const [pending, setPending] = useState<Record<BoxName, string>>(NO_TEXT);

  // A navigation (back/forward, cell link) replaces whatever was being typed.
  useEffect(() => {
    setDraft(spec);
    setPending(NO_TEXT);
  }, [spec]);

  function setTerms(box: BoxName, terms: string[]) {
    setDraft((d) => ({ ...d, terms: { ...d.terms, [box]: terms } }));
  }

  function setText(box: BoxName, text: string) {
    setPending((p) => ({ ...p, [box]: text }));
  }

  /** Submit with whatever is still typed in the boxes, so Enter is never required. */
  function submit(e?: FormEvent) {
    e?.preventDefault();
    const terms = { ...draft.terms };
    for (const box of BOXES) {
      if (pending[box].trim()) terms[box] = normalizeTerms([...terms[box], pending[box]]);
    }
    setPending(NO_TEXT);
    onSubmit({ ...draft, terms });
  }

  return (
    <form className="query-form" onSubmit={submit}>
      <p className="intro">
        Type a name or its speedrun.com abbreviation and press Enter to add it. Prefix a term with <code>-</code> to
        exclude it. Series, games and platforms combine; leave them empty for all games.
      </p>

      <div className="grid-container">
        {BOXES.map((box) => (
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
          />
        ))}
      </div>

      <label htmlFor="request-type">Request Type:</label>
      <div className="request">
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
        <button type="submit" className="submit">
          Submit
        </button>
      </div>

      <p className="credits">
        Created by <a href="https://www.speedrun.com/users/Maximum">Maximum</a>
        <span> &bull; </span>
        <a href="https://www.desmos.com/calculator/uvredthnmv">Formula</a>
        <span> &bull; </span>
        <a href="https://github.com/ItsMaximum/SpeedStats">Source Code</a>
      </p>
    </form>
  );
}
