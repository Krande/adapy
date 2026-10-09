// The change feed's fetch side: `GET /api/scopes/{scope}/source-nodes`
// (`ada/comms/rest/routes/source_nodes.py`). Read-only: the browser only ever
// ASKS this feed. Writing belongs to a provider's own sweep, through the
// worker's `source_nodes` facade or the POST route the GET sits beside in
// that same file -- never a path this module exposes.
//
// Returns the WIRE shape parsed into `@/assets/changes`'s model; nothing else
// in the frontend reads `WireSourceNodesRefsResponse` directly.

import { runtime } from "@/runtime/config";

import { sourceNodesAnswerFromWire, type SourceNodeRow, type SourceNodesAnswer } from "@/assets/changes";
import type { WireSourceNodesRefsResponse } from "@/assets/types";

import { assetProviderCollections, changeFeedSource } from "@/services/assetScopeCollections";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";
import { pluginsApi } from "./plugins";

function base(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/source-nodes`;
}

// provider id -> its declared `asset_change_source` template. Read once from `GET /plugins` and
// kept: a declaration does not change while a page is open. A failed read is not cached, so the
// next ask retries; until then the feed is read under the provider id, as it always was.
let declared: Promise<Map<string, string>> | null = null;

function declaredSources(): Promise<Map<string, string>> {
  if (!declared) {
    declared = pluginsApi
      .listBackendPlugins()
      .then((r) => {
        const out = new Map<string, string>();
        for (const p of assetProviderCollections(r.plugins ?? [])) if (p.changeSource) out.set(p.providerId, p.changeSource);
        return out;
      })
      .catch(() => {
        declared = null;
        return new Map<string, string>();
      });
  }
  return declared;
}

/** The feed source to ask for `provider`'s nodes in `collection` (see `changeFeedSource`). */
export async function feedSourceFor(provider: string, collection?: string | null): Promise<string> {
  return changeFeedSource((await declaredSources()).get(provider), provider, collection);
}

/** Test seam: forget the cached declarations. */
export function resetDeclaredSources(): void {
  declared = null;
}

// The route defaults its own `limit` to 1000 and 400s a `refs=` list longer
// than that; this keeps every request comfortably under it (headroom for the
// `source`/URL overhead) rather than trusting every caller to know the
// server's cap. Callers still fetch lazily per spine (§Decision 4's frontend
// half) -- chunking here is a safety net for one large spine, not licence to
// ask for a whole 41k-row tree in one go.
const REFS_PER_REQUEST = 900;

function chunk<T>(items: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/**
 * Ask the change feed about exactly these refs (an export root's subject id,
 * or any node ref beneath it -- the route makes no distinction).
 *
 * Returns `null` on a 503, mapping the deployment's "this runs without a
 * database" answer (`_source_nodes_pool`) onto `no-feed`, one of the change
 * feed's four legitimate states (`@/assets/changes`) -- NOT a transport
 * fault. This is deliberately not error-swallowing: `no-feed` is a
 * first-class value `classifyChanges` renders as its own state (nothing
 * marked `current`, which a caught-and-ignored exception would risk if a
 * caller's `catch` silently kept stale data instead); every other status
 * code still throws through `jsonOrThrow` exactly as any other route in
 * `services/api/` does, so a real fault (500, a network error, an
 * authorization failure) is still a fault and still surfaces as one.
 */
export async function getSourceNodes(
  scope: ScopeUrl,
  source: string,
  refs: readonly string[],
  collection?: string | null,
): Promise<SourceNodesAnswer | null> {
  if (refs.length === 0) return { source, rows: new Map(), unknown: new Set() };
  // `source` is the provider id the caller groups by; the feed may be recorded under a source the
  // provider declared per collection. The answer is returned under the provider id regardless.
  const feed = await feedSourceFor(source, collection);
  const answerRows = new Map<string, SourceNodeRow>();
  const unknown = new Set<string>();
  for (const part of chunk(refs, REFS_PER_REQUEST)) {
    const q = new URLSearchParams({ source: feed, refs: part.join(",") });
    const r = await authedFetch(`${base(scope)}?${q}`);
    if (r.status === 503) return null;
    const wire = await jsonOrThrow<WireSourceNodesRefsResponse>(r, `getSourceNodes(${source})`);
    const answer = sourceNodesAnswerFromWire(wire);
    for (const [ref, row] of answer.rows) answerRows.set(ref, row);
    for (const u of answer.unknown) unknown.add(u);
  }
  return { source, rows: answerRows, unknown };
}

export const sourceNodesApi = { getSourceNodes };
