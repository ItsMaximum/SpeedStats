import { startTransition, useEffect, useRef, useState, type MouseEvent } from "react";
import { ApiError, fetchMeta, fetchQuery, queryUrl, type MetaOut, type QueryOut } from "./api";
import { DataCounts } from "./components/LastUpdated";
import { MenuButton } from "./components/MenuButton";
import { QueryForm } from "./components/QueryForm";
import { ResultsTable } from "./components/ResultsTable";
import { Spinner } from "./components/Spinner";
import { Warnings } from "./components/Warnings";
import {
  BOXES,
  MAX_LIMIT,
  canonicalize,
  splitTerm,
  toSearch,
  type BoxName,
  type QuerySpec,
  type TermInfo,
} from "./query";
import { navigate, replace, useUrlState } from "./useUrlState";

/** Full names for the chips, by box and lowercased term (both the term as given and its abbreviation). */
type Names = Record<BoxName, Record<string, string>>;

const NO_NAMES: Names = { series: {}, games: {}, platforms: {}, players: {}, locations: {} };

/** The terms of `spec` that the API could not match at all (they are absent from `term_info`). */
function invalidTerms(spec: QuerySpec, info: TermInfo): Record<BoxName, string[]> {
  const out = { series: [], games: [], platforms: [], players: [], locations: [] } as Record<BoxName, string[]>;
  for (const box of BOXES) {
    const known = new Set(Object.keys(info[box] ?? {}).map((t) => t.toLowerCase()));
    out[box] = spec.terms[box].map((t) => splitTerm(t).bare.toLowerCase()).filter((t) => !known.has(t));
  }
  return out;
}

function withNames(names: Names, info: TermInfo): Names {
  const next = { ...names };
  for (const box of BOXES) {
    const entries = Object.entries(info[box] ?? {});
    if (!entries.length) continue;
    next[box] = { ...next[box] };
    for (const [term, { name, slug }] of entries) {
      next[box][term.toLowerCase()] = name;
      if (slug) next[box][slug.toLowerCase()] = name;
    }
  }
  return next;
}

export default function App() {
  const [spec, setSpec, search] = useUrlState();
  const [result, setResult] = useState<QueryOut | null>(null);
  const [meta, setMeta] = useState<MetaOut | null>(null);

  // the form shows "last updated" before the first result arrives
  useEffect(() => {
    const controller = new AbortController();
    fetchMeta(controller.signal)
      .then(setMeta)
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [names, setNames] = useState<Names>(NO_NAMES);
  const [invalid, setInvalid] = useState<Record<BoxName, string[]>>();
  const [resolvedFor, setResolvedFor] = useState<string>(); // the URL the result on screen answers
  const [clearCount, setClearCount] = useState(0);
  const [formHasValues, setFormHasValues] = useState(false);
  // the URL a result was just rewritten to (typed names -> abbreviations); that result already answers it
  const skipFetch = useRef<string | null>(null);

  useEffect(() => {
    if (search === skipFetch.current) {
      skipFetch.current = null;
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchQuery(spec, controller.signal)
      .then((out) => {
        document.title = `SpeedStats - ${out.title}`;
        const canonical = toSearch(canonicalize(spec, out.term_info));
        if (window.location.pathname + search !== "/" + canonical) {
          skipFetch.current = canonical;
          replace(canonical);
        }
        startTransition(() => {
          setResult(out);
          setMeta(out.meta);
          setNames((n) => withNames(n, out.term_info));
          setInvalid(invalidTerms(spec, out.term_info));
          setResolvedFor(canonical);
          setLoading(false);
        });
      })
      .catch((e: unknown) => {
        if ((e as Error).name === "AbortError") return;
        setError(e instanceof ApiError ? e.message : "Could not reach the server.");
        setResolvedFor(toSearch(spec)); // let the form adopt the query so it can be edited and retried
        setLoading(false);
      });
    return () => controller.abort();
  }, [spec, search]);

  // Links inside the results are queries on this page; navigate in place instead of reloading.
  function onResultsClick(e: MouseEvent) {
    const a = (e.target as HTMLElement).closest("a");
    if (!a || !a.getAttribute("href")?.startsWith("?") || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    navigate(a.getAttribute("href")!);
    window.scrollTo({ top: 0 });
  }

  function copyLink() {
    void navigator.clipboard.writeText(window.location.origin + "/" + search).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  function showMore() {
    const more: QuerySpec = { ...spec, limit: MAX_LIMIT };
    setSpec(more);
  }
  
  return (
    <>
      {!result && !error ? (
        // first open: nothing is shown until the first query is back, so chips never appear without their names
        <div className="site-loading" role="status" aria-label="Loading">
          <Spinner size={40} />
        </div>
      ) : (
        <QueryForm
          spec={spec}
          onSubmit={setSpec}
          meta={meta}
          loading={loading}
          names={names}
          invalid={invalid}
          resolvedFor={resolvedFor}
          clearCount={clearCount}
          onHasValues={setFormHasValues}
        />
      )}

      <section className="results" onClick={onResultsClick} aria-busy={loading}>
        {result && (
          <div className="results-header">
            <div className="actions">
              <button
                type="button"
                className="clear-button"
                disabled={!formHasValues}
                onClick={() => setClearCount((n) => n + 1)}
              >
                Clear
              </button>
              <MenuButton label={copied ? "Copied!" : "Share"}>
                <button type="button" role="menuitem" onClick={copyLink}>
                  Copy link
                </button>
              </MenuButton>
              <MenuButton label="Export">
                <a role="menuitem" href={queryUrl(spec, "csv")} download>
                  CSV
                </a>
                <a role="menuitem" href={queryUrl(spec)} target="_blank" rel="noreferrer">
                  JSON
                </a>
              </MenuButton>
            </div>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        {result && <Warnings warnings={result.warnings} />}
        {result && !error && <ResultsTable result={result} />}
        {result?.truncated && (
          <p className="truncated">
            Showing the first {result.rows.length.toLocaleString()} rows.{" "}
            {spec.limit < MAX_LIMIT && (
              <button type="button" className="linklike" onClick={showMore}>
                Show up to {MAX_LIMIT.toLocaleString()}
              </button>
            )}
          </p>
        )}
        {result && <DataCounts meta={result.meta} />}
      </section>
    </>
  );
}
