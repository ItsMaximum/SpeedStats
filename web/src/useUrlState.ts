import { useCallback, useMemo, useSyncExternalStore } from "react";
import { parseQuery, toSearch, type QuerySpec } from "./query";

const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

function getSearch() {
  return window.location.search;
}

/** Navigate to a new query string (pushState) and notify subscribers. */
export function navigate(search: string) {
  const target = "/" + (search.startsWith("?") ? search : "?" + search);
  if (window.location.pathname + window.location.search === target) return;
  window.history.pushState(null, "", target);
  emit();
}

/** The current query, parsed from the address bar; every state of the page is a shareable link. */
export function useUrlState(): [QuerySpec, (spec: QuerySpec) => void, string] {
  const search = useSyncExternalStore(subscribe, getSearch, () => "");
  const spec = useMemo(() => parseQuery(search), [search]);
  const set = useCallback((next: QuerySpec) => navigate(toSearch(next)), []);
  return [spec, set, search];
}
