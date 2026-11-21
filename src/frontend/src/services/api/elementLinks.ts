// Per-element associations (`routes/element_links.py`): the clash contacts, connections, welds,
// simulation subjects and simulation runs filed under a model's elements, and under each of their
// ancestors -- so "what has been found, built and simulated under this node" is one call.
//
// WIRE shapes only.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";
import type { SimulationBatchRef } from "./simulations";

/** The kinds of link. */
export type ElementLinkKind = "contact" | "connection" | "weld" | "subject" | "sim_run";

// Kept with the selection snapshot (dependency-free, on the root entry), named here too.
import type { ElementNodeRef } from "@/plugins/propertyProviders";

export type { ElementNodeRef };

/** One element's link. `data` is the kind's own (see `ada.assoc.records`). */
export interface ElementLink {
  readonly kind: ElementLinkKind;
  readonly element_key: string;
  /** The document it came from: a run key, a detail record, a subject key, a run record. */
  readonly doc_key: string;
  readonly anc?: readonly string[];
  readonly ck?: string;
  readonly group?: string;
  readonly created_at?: string;
  readonly fp?: string;
  readonly data: Readonly<Record<string, unknown>>;
  /** The element's geometry changed since the link was made. */
  readonly stale: boolean;
  /** The element is no longer in its model (renamed or gone). */
  readonly orphan: boolean;
  /** A newer link of the same kind exists for the same element and connection. */
  readonly superseded: boolean;
}

/** The links of one connection (by its ck) or one contact pair. */
export interface ElementLinkGroup {
  readonly group: string;
  readonly ck: string | null;
  readonly kinds: Readonly<Partial<Record<ElementLinkKind, number>>>;
  readonly rows: readonly ElementLink[];
}

export interface ElementLinksSummary {
  readonly rows: number;
  readonly elements: number;
  readonly groups: number;
  readonly kinds: Readonly<Partial<Record<ElementLinkKind, number>>>;
  /** Simulation-run links by status. */
  readonly sim_runs: Readonly<Record<string, number>>;
  readonly stale: number;
  readonly orphan: number;
  readonly superseded: number;
}

export interface ElementLinksPage {
  readonly summary: ElementLinksSummary;
  readonly groups: readonly ElementLinkGroup[];
  /** The cursor of the next page; null at the end. */
  readonly next: string | null;
  /** The element keys the nodes named. */
  readonly keys: readonly string[];
  /** A run on this page is queued or running: worth asking again. */
  readonly active: boolean;
  /** The scope's links are being built from its documents for the first time. */
  readonly reindexing: boolean;
}

export interface ElementLinksQuery {
  readonly nodes?: readonly ElementNodeRef[];
  readonly group_id?: string;
  readonly keys?: readonly string[];
  readonly cks?: readonly string[];
  readonly kinds?: readonly ElementLinkKind[];
  readonly include_stale?: boolean;
  readonly latest_only?: boolean;
  readonly cursor?: string | null;
  /** Groups per page, at most 500. */
  readonly limit?: number;
}

export interface ConnectionLinks {
  readonly ck: string;
  readonly members: readonly string[];
  readonly connections: readonly ElementLink[];
  readonly welds: readonly ElementLink[];
  readonly subjects: readonly ElementLink[];
  readonly sim_runs: readonly ElementLink[];
  readonly contacts: readonly ElementLink[];
}

export type SimulationBatchPolicy = "missing" | "failed" | "all";

export interface StartElementsBatchBody {
  readonly nodes?: readonly ElementNodeRef[];
  readonly group_id?: string;
  readonly cks?: readonly string[];
  readonly provider: string;
  readonly parameters?: Readonly<Record<string, unknown>>;
  readonly kind?: string;
  readonly policy?: SimulationBatchPolicy;
  readonly dry_run?: boolean;
  /** Pass the answer's batch back with `cursor` for the next page, so one press is one batch. */
  readonly batch?: SimulationBatchRef;
  readonly cursor?: string | null;
  readonly limit?: number;
}

export interface StartElementsBatchResult {
  readonly batch: SimulationBatchRef;
  readonly started: ReadonlyArray<{ readonly ck: string; readonly run_id: string | null }>;
  readonly skipped: ReadonlyArray<{ readonly ck: string; readonly reason: string }>;
  readonly failed: ReadonlyArray<{ readonly ck: string; readonly error: string }>;
  readonly next: string | null;
  readonly dry_run: boolean;
}

export interface MakeSubjectsResult {
  readonly jobs: ReadonlyArray<{ readonly job_id: string; readonly detail_id: string; readonly cks: readonly string[] }>;
  readonly missing: readonly string[];
  readonly have: readonly string[];
}

const scoped = (scope: ScopeUrl) => `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}`;

async function post<T>(url: string, body: unknown, what: string): Promise<T> {
  const r = await authedFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return jsonOrThrow<T>(r, what);
}

/** The links under some nodes (or a saved group's members, exact elements, connections), a page of
 *  groups at a time. */
export async function queryElementLinks(scope: ScopeUrl, query: ElementLinksQuery): Promise<ElementLinksPage> {
  return post<ElementLinksPage>(`${scoped(scope)}/elements/links/query`, query, "queryElementLinks");
}

/** One element's links, and its descendants'. */
export async function elementLinks(
  scope: ScopeUrl,
  key: string,
  opts: { readonly kinds?: readonly ElementLinkKind[]; readonly cursor?: string | null; readonly limit?: number } = {},
): Promise<ElementLinksPage> {
  const params = new URLSearchParams();
  if (opts.kinds?.length) params.set("kinds", opts.kinds.join(","));
  if (opts.cursor) params.set("cursor", opts.cursor);
  if (opts.limit) params.set("limit", String(opts.limit));
  const query = params.toString();
  const r = await authedFetch(`${scoped(scope)}/elements/${encodeURIComponent(key)}/links${query ? `?${query}` : ""}`);
  return jsonOrThrow<ElementLinksPage>(r, "elementLinks");
}

export const elementLinksApi = {
  query: queryElementLinks,
  element: elementLinks,

  /** One connection's members, welds, subject and runs. */
  async connection(scope: ScopeUrl, ck: string): Promise<ConnectionLinks> {
    const r = await authedFetch(`${scoped(scope)}/connections/${encodeURIComponent(ck)}`);
    return jsonOrThrow<ConnectionLinks>(r, "connectionLinks");
  },

  /** Make the missing simulation subjects of the built connections under some nodes. */
  async makeSubjects(
    scope: ScopeUrl,
    body: Pick<StartElementsBatchBody, "nodes" | "group_id" | "cks"> & { readonly dry_run?: boolean },
  ): Promise<MakeSubjectsResult> {
    return post<MakeSubjectsResult>(`${scoped(scope)}/elements/subjects`, body, "makeSubjects");
  },

  /** Simulate every connection under some nodes that `policy` says needs it, in one batch. */
  async startBatch(scope: ScopeUrl, body: StartElementsBatchBody): Promise<StartElementsBatchResult> {
    return post<StartElementsBatchResult>(`${scoped(scope)}/simulations/batch`, body, "startSimulationBatch");
  },

  /** Build the scope's links again from its documents (in the background). */
  async reindex(scope: ScopeUrl): Promise<void> {
    await post<unknown>(`${scoped(scope)}/elements/links/reindex`, {}, "reindexElementLinks");
  },
};

export type ElementLinksApi = typeof elementLinksApi;

/** The newest simulation-run link of a group (the connection's latest run), or null. */
export function latestRun(group: Pick<ElementLinkGroup, "rows">): ElementLink | null {
  let best: ElementLink | null = null;
  for (const row of group.rows) {
    if (row.kind !== "sim_run") continue;
    if (!best || String(row.created_at ?? "") > String(best.created_at ?? "")) best = row;
  }
  return best;
}
