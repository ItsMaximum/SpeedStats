import { useEffect, useState, type MouseEvent } from "react";
import { ApiError, fetchQuery, queryUrl, type QueryOut } from "./api";
import { LastUpdated } from "./components/LastUpdated";
import { QueryForm } from "./components/QueryForm";
import { ResultsTable } from "./components/ResultsTable";
import { Warnings } from "./components/Warnings";
import { MAX_LIMIT, type QuerySpec } from "./query";
import { navigate, useUrlState } from "./useUrlState";

export default function App() {
  const [spec, setSpec, search] = useUrlState();
  const [result, setResult] = useState<QueryOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchQuery(spec, controller.signal)
      .then((out) => {
        setResult(out);
        document.title = `SpeedStats - ${out.title}`;
      })
      .catch((e: unknown) => {
        if ((e as Error).name === "AbortError") return;
        setError(e instanceof ApiError ? e.message : "Could not reach the server.");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [spec]);

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
    <main>
      <h1 className="brand">
        <a href="/">
          <img src="/logo.png" alt="" width={44} height={44} />
          SpeedStats
        </a>
      </h1>
      <QueryForm spec={spec} onSubmit={setSpec} />

      <section className="results" onClick={onResultsClick} aria-busy={loading}>
        {result && (
          <div className="results-header">
            <h2>{result.title}</h2>
            <div className="actions">
              <button type="button" onClick={copyLink}>
                {copied ? "Copied!" : "Copy link"}
              </button>
              <a href={queryUrl(spec, "csv")} download>
                CSV
              </a>
              <a href={queryUrl(spec)} target="_blank" rel="noreferrer">
                JSON
              </a>
            </div>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        {result && <Warnings warnings={result.warnings} />}
        {loading && <p className="loading">Loading…</p>}
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
        {result && <LastUpdated meta={result.meta} />}
      </section>
    </main>
  );
}
