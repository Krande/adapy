// The links under some nodes, for a component: the first page at once, more on `loadMore`, and the
// pages already read again every few seconds while a run on them is queued or running (or the
// scope's links are still being built), so a status follows the run without a reload.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  queryElementLinks,
  type ElementLinkGroup,
  type ElementLinksPage,
  type ElementLinksQuery,
  type ElementLinksSummary,
} from "@/services/api/elementLinks";

/** How often the pages are read again while something on them is going [ms]. */
export const ELEMENT_LINKS_REFRESH_MS = 5000;

export interface ElementLinksState {
  readonly summary: ElementLinksSummary | null;
  readonly groups: readonly ElementLinkGroup[];
  /** There are more groups than read so far. */
  readonly more: boolean;
  readonly loading: boolean;
  readonly error: string | null;
  /** A run on the pages read is queued or running. */
  readonly active: boolean;
  readonly reindexing: boolean;
  loadMore(): void;
  refresh(): void;
}

export interface UseElementLinksOptions {
  /** Groups per page (default 50). */
  readonly limit?: number;
  /** Read again while active (default `ELEMENT_LINKS_REFRESH_MS`); 0 never. */
  readonly refreshMs?: number;
  /** The query function, for a test. */
  readonly query?: typeof queryElementLinks;
}

/** A stable key for a query: a new object with the same content is the same query. */
function keyOf(scope: string | null, query: ElementLinksQuery | null): string | null {
  return scope && query ? `${scope}|${JSON.stringify(query)}` : null;
}

/** The links `query` names in `scope`; nothing while either is null. */
export function useElementLinks(
  scope: string | null,
  query: ElementLinksQuery | null,
  opts: UseElementLinksOptions = {},
): ElementLinksState {
  const limit = opts.limit ?? 50;
  const refreshMs = opts.refreshMs ?? ELEMENT_LINKS_REFRESH_MS;
  const run = opts.query ?? queryElementLinks;
  const key = keyOf(scope, query);
  const [pages, setPages] = useState<ElementLinksPage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const current = useRef<string | null>(null);
  const queryRef = useRef(query);
  queryRef.current = query;

  // The first page, again for a new query.
  useEffect(() => {
    current.current = key;
    setPages([]);
    setError(null);
    if (!key || !scope || !queryRef.current) return;
    let cancelled = false;
    setLoading(true);
    run(scope, { ...queryRef.current, limit, cursor: null })
      .then((page) => {
        if (!cancelled && current.current === key) setPages([page]);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key, limit, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  const active = pages.some((p) => p.active || p.reindexing);

  // Every page read, again, while something on them is going.
  useEffect(() => {
    if (!active || !refreshMs || !scope || !queryRef.current) return;
    const timer = window.setTimeout(() => {
      const q = queryRef.current;
      if (!q || current.current !== key) return;
      const cursors = [null, ...pages.slice(0, -1).map((p) => p.next)];
      void Promise.all(cursors.map((cursor) => run(scope, { ...q, limit, cursor })))
        .then((fresh) => {
          if (current.current === key) setPages(fresh);
        })
        .catch(() => {
          /* the next tick tries again */
        });
    }, refreshMs);
    return () => window.clearTimeout(timer);
  }, [active, pages, key, limit, refreshMs]); // eslint-disable-line react-hooks/exhaustive-deps

  const last = pages[pages.length - 1] ?? null;
  const loadMore = useCallback(() => {
    if (!scope || !queryRef.current || !last?.next || loading) return;
    setLoading(true);
    run(scope, { ...queryRef.current, limit, cursor: last.next })
      .then((page) => {
        if (current.current === key) setPages((ps) => [...ps, page]);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [scope, key, last, limit, loading, run]);

  return {
    summary: pages[0]?.summary ?? null,
    groups: pages.flatMap((p) => p.groups),
    more: !!last?.next,
    loading,
    error,
    active: pages.some((p) => p.active),
    reindexing: pages.some((p) => p.reindexing),
    loadMore,
    refresh: () => setTick((n) => n + 1),
  };
}
