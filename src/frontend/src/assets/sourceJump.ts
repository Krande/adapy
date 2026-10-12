// From an element of an exported file back to its node in the Sources tree -- the pure half.
//
// WHAT THE FILE CARRIES. A search-result export writes one IFC per hit, rooted at the top of its
// tree (top level -> ... -> hit -> members), and every element that came from a source node carries
// an `ADA_Source` property set: provider, collection, node id, full name and the SCOPE the search
// ran in. The viewer's tree holds the file's names and nesting but not its property sets, so the
// sets come from the file's provenance record (`/files/meta`, `elements`: the sets by IFC name,
// read from the sidecar or, when that is gone, from the file itself).
//
// WHICH NODE. The selected element's own set when it has one, else its nearest ancestor's -- a
// sub-assembly the index had no row for, or a member that matched none, still sits under a level
// that did. The PATH is the sets of the ancestors above that one, root first: exactly the levels
// the export wrote above it, which is the chain the Sources tree is walked down (`walkHitPath`).
// A record without per-element sets (a file written before they were recorded) still names its
// hit and the hit's path; the levels are then matched by full name, and failing even that the
// file's own hit is offered.
//
// WHICH SCOPE. The tree shows the CURRENT scope, so a node of another one is revealed after
// switching to it -- when the user can see that scope at all; otherwise the reason is said.
//
// No fetching, no stores: `utils/tree_view/revealInSourceScope.ts` does the I/O.

import type { SearchHit, SearchPathEntry } from "@/services/api/attributeSearch";
import type { FileMeta, FileMetaElement, SourceSet } from "@/services/api/searchExport";

/** One row of the viewer's selection tree, as the jump needs it. */
export interface TreeStep {
  id: string;
  name: string;
}

export interface SourceJump {
  /** What `revealSearchHit` walks to. */
  hit: SearchHit;
  /** The scope the node lives in (path form); null when the record does not say. */
  scope: string | null;
  /** Whose set it is: the selected element's, an ancestor's, or (no element matched) the file's hit. */
  via: "self" | "ancestor" | "file";
  /** The tree row (or, for `file`, the hit's name) the set belongs to. */
  element: string;
}

/** A name as the two sides may spell it: trimmed, without the leading `/` a reader strips, and
 *  without the ` [<node id>]` suffix the export adds to tell same-named levels apart. */
export function normaliseName(name: string | null | undefined): string {
  return String(name ?? "")
    .trim()
    .replace(/\s\[[^\]]*\]$/, "")
    .replace(/^\/+/, "")
    .trim();
}

/** The record's per-element sets; for an older record, its hit's path and the hit, by full name. */
export function elementsOf(meta: FileMeta | null): FileMetaElement[] {
  if (!meta) return [];
  const listed = (meta.elements ?? []).filter((e) => e && e.source && e.source.node_id);
  if (listed.length) return listed;
  const s = meta.source;
  if (!s || !s.node_id) return [];
  const base = { provider: s.provider, scope: s.scope ?? null, collection: s.collection };
  const levels: FileMetaElement[] = (s.path ?? [])
    .filter((p) => p.node_id !== s.node_id)
    .map((p) => ({
      name: p.full_name || p.node_id,
      guid: null,
      role: "level",
      source: { ...base, node_id: p.node_id, full_name: p.full_name || null, source_ref: null },
    }));
  return [
    ...levels,
    {
      name: s.full_name || s.node_id,
      guid: null,
      role: "hit",
      source: { ...base, node_id: s.node_id, full_name: s.full_name, source_ref: s.source_ref },
    },
  ];
}

/** Look a tree row's set up: by the IFC GlobalId when the tree keys rows by it, else by name --
 *  exact first, then as normalised. Same-named elements: the first in record order (root first). */
export function sourceLookup(elements: readonly FileMetaElement[]): (step: TreeStep) => FileMetaElement | null {
  const byGuid = new Map<string, FileMetaElement>();
  const exact = new Map<string, FileMetaElement>();
  const loose = new Map<string, FileMetaElement>();
  for (const e of elements) {
    if (e.guid && !byGuid.has(e.guid)) byGuid.set(e.guid, e);
    if (!exact.has(e.name)) exact.set(e.name, e);
    for (const n of [normaliseName(e.name), normaliseName(e.source.full_name)]) {
      if (n && !loose.has(n)) loose.set(n, e);
    }
  }
  return (step) => byGuid.get(step.id) ?? exact.get(step.name) ?? loose.get(normaliseName(step.name)) ?? null;
}

function pathEntry(src: SourceSet): SearchPathEntry {
  return { node_id: String(src.node_id), full_name: src.full_name || String(src.node_id), kind: null, depth: null };
}

function hitOf(src: SourceSet, path: SearchPathEntry[]): SearchHit {
  return {
    provider: src.provider ?? "",
    collection: src.collection ?? "",
    node_id: String(src.node_id),
    parent_id: path.length ? path[path.length - 1].node_id : null,
    full_name: src.full_name || String(src.node_id),
    source_ref: src.source_ref ?? null,
    kind: null,
    depth: null,
    attrs: {},
    path,
  };
}

/**
 * The source node for the element at `chain[0]` -- `chain` is the selected row and its ancestors,
 * NEAREST FIRST, up to (and including) the loaded file's root row. null when neither the element,
 * any ancestor, nor the record's hit names a node with a provider and a collection.
 */
export function sourceJumpFor(chain: readonly TreeStep[], meta: FileMeta | null): SourceJump | null {
  const elements = elementsOf(meta);
  if (!elements.length) return null;
  const lookup = sourceLookup(elements);
  const sets = chain.map((step) => lookup(step));
  const at = sets.findIndex((e) => e !== null && !!e.source.provider && !!e.source.collection);
  const fileScope = meta?.source?.scope ?? null;
  if (at < 0) {
    const s = meta?.source;
    if (!s || !s.node_id || !s.provider || !s.collection) return null;
    const path = (s.path ?? []).filter((p) => p.node_id !== s.node_id);
    return {
      hit: hitOf({ ...s, scope: fileScope }, path),
      scope: fileScope,
      via: "file",
      element: s.full_name || s.node_id,
    };
  }
  const target = sets[at]!;
  // Ancestors' sets, root first; one entry per node (a level and a same-named part are one node).
  const path: SearchPathEntry[] = [];
  const seen = new Set<string>([String(target.source.node_id)]);
  for (let i = sets.length - 1; i > at; i--) {
    const e = sets[i];
    if (!e || seen.has(String(e.source.node_id))) continue;
    // A set of another provider/collection is not on this node's path.
    if (e.source.provider !== target.source.provider || e.source.collection !== target.source.collection) continue;
    seen.add(String(e.source.node_id));
    path.push(pathEntry(e.source));
  }
  // An older record knows the hit's path though the tree did not show its levels by those names.
  if (!path.length && meta?.source?.node_id === target.source.node_id) {
    path.push(...(meta.source.path ?? []).filter((p) => p.node_id !== target.source.node_id));
  }
  const scope = target.source.scope ?? fileScope;
  return {
    hit: hitOf(target.source, path),
    scope,
    via: at === 0 ? "self" : "ancestor",
    element: chain[at].name,
  };
}

// --- the scope ------------------------------------------------------------------------------------

export interface ScopeLike {
  kind: string;
  id: string | null;
  name: string;
}

export type ScopeDecision<S extends ScopeLike = ScopeLike> =
  | { kind: "stay" }
  | { kind: "switch"; to: S }
  | { kind: "refuse"; reason: string };

/** How a scope's path form names it to a person: `project:<id>` -> the project's name when known. */
function scopeLabel(url: string): string {
  if (url === "shared") return "Shared";
  if (url.startsWith("user:")) return "a personal scope";
  const i = url.indexOf(":");
  return i > 0 ? `${url.slice(0, i)} ${url.slice(i + 1)}` : url;
}

/**
 * Whether revealing a node of `source` needs a scope switch. `current` is the current scope's path
 * form, `available` the scopes the user can open, `urlOf` their path forms (`scopeUrlPart`).
 * Unknown source scope = the current one (all a record without one can mean). A scope the user
 * cannot open is refused with the reason -- the tree would only fail to load there.
 */
export function decideScope<S extends ScopeLike>(
  source: string | null | undefined,
  current: string,
  available: readonly S[],
  urlOf: (s: S) => string,
): ScopeDecision<S> {
  if (!source || source === current) return { kind: "stay" };
  const to = available.find((s) => urlOf(s) === source);
  if (to) return { kind: "switch", to };
  return {
    kind: "refuse",
    reason: `The source node is in ${scopeLabel(source)}, which you do not have access to`,
  };
}
