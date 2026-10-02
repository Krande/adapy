// How a collection's tree is DRAWN, as opposed to what it holds.
//
// Three settings, all over the provider's opaque `kind` (compared trimmed and
// case-insensitively), and core branches on none of them by value:
//
//   flattenKinds  rows of these kinds are not drawn; their children take their
//                 place. For a system whose model hangs under one redundant
//                 container -- a design world above every site -- this is "start
//                 the tree one level down". Kind-based rather than depth-based on
//                 purpose: the same collection mixes spines that start at that
//                 container with spines that start below it, and "drop one level"
//                 would drop the wrong row from the second kind.
//   rootKinds     an allow-list for the TOP level only (null = every kind). Hides
//                 the clutter a model keeps beside its real tops. Stands down
//                 rather than empty the tree, and is off while searching -- a
//                 search is a question about everything.
//   outOfScope    node ids hidden with everything under them, shared by the
//                 scope; `showHidden` draws them again, marked.
//
// WHERE EACH COMES FROM. The provider knows its data, so it publishes DEFAULTS
// with its collection index (`view` on the slice -- `parseViewHints`). The
// scope may SAVE its own (`assets/_view/<collection>.json` -- `parseViewDoc`),
// and a saved field overrides the provider's; a field the save leaves out keeps
// the provider's. Nothing here is a permission: it changes what is drawn.
//
// Pure: `displayHierarchy` returns a hierarchy `flattenVisible` walks as is.

import type { Hierarchy, HierarchyNode } from "@/assets/hierarchy";

export const VIEW_DOC_SCHEMA = "ada.assets/view@1";

/** The provider's defaults, as published on a collection index. */
export interface TreeViewHints {
  readonly flattenKinds?: readonly string[];
  readonly rootKinds?: readonly string[];
}

/** What a scope saved. Every field optional: absent keeps the provider's. */
export interface TreeViewDoc {
  readonly schema: typeof VIEW_DOC_SCHEMA;
  readonly flatten_kinds?: readonly string[];
  /** `null` saved explicitly means "every kind", overriding a provider default. */
  readonly root_kinds?: readonly string[] | null;
  readonly out_of_scope?: readonly string[];
  readonly updated_at?: string;
}

export interface TreeViewSettings {
  readonly flattenKinds: ReadonlySet<string>;
  readonly rootKinds: ReadonlySet<string> | null;
  readonly outOfScope: ReadonlySet<string>;
  /** Where the kind settings came from, for the panel to say so. */
  readonly source: "saved" | "provider" | "none";
}

export const NO_VIEW: TreeViewSettings = Object.freeze({
  flattenKinds: new Set<string>(),
  rootKinds: null,
  outOfScope: new Set<string>(),
  source: "none",
});

/** The comparison form of a kind. */
export function normKind(kind: string): string {
  return kind.trim().toLowerCase();
}

function stringList(raw: unknown): string[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  return raw.filter((k): k is string => typeof k === "string" && k.trim().length > 0).map((k) => k.trim());
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/** The `view` object off a hierarchy slice, or null. Unknown keys are ignored
 *  and a malformed field is dropped, so a provider cannot break the tab. */
export function parseViewHints(raw: unknown): TreeViewHints | null {
  if (!isRecord(raw)) return null;
  const flattenKinds = stringList(raw.flatten_kinds);
  const rootKinds = stringList(raw.root_kinds);
  if (!flattenKinds && !rootKinds) return null;
  return { ...(flattenKinds ? { flattenKinds } : {}), ...(rootKinds ? { rootKinds } : {}) };
}

/** A saved view document, or null when absent or not one. */
export function parseViewDoc(raw: unknown): TreeViewDoc | null {
  let doc: unknown = raw;
  if (typeof raw === "string") {
    try {
      doc = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!isRecord(doc) || doc.schema !== VIEW_DOC_SCHEMA) return null;
  const out: { -readonly [K in keyof TreeViewDoc]: TreeViewDoc[K] } = { schema: VIEW_DOC_SCHEMA };
  const flatten = stringList(doc.flatten_kinds);
  if (flatten) out.flatten_kinds = flatten;
  if (doc.root_kinds === null) out.root_kinds = null;
  else {
    const roots = stringList(doc.root_kinds);
    if (roots) out.root_kinds = roots;
  }
  const hidden = stringList(doc.out_of_scope);
  if (hidden) out.out_of_scope = hidden;
  if (typeof doc.updated_at === "string") out.updated_at = doc.updated_at;
  return out;
}

/** Saved fields over the provider's, field by field. */
export function resolveTreeView(doc: TreeViewDoc | null, hints: TreeViewHints | null): TreeViewSettings {
  const kinds = (list: readonly string[] | undefined) => new Set((list ?? []).map(normKind));
  const savedKinds = doc !== null && (doc.flatten_kinds !== undefined || doc.root_kinds !== undefined);
  const flatten = doc?.flatten_kinds ?? hints?.flattenKinds;
  const roots = doc && doc.root_kinds !== undefined ? doc.root_kinds : hints?.rootKinds;
  return {
    flattenKinds: kinds(flatten),
    rootKinds: roots === null || roots === undefined || roots.length === 0 ? null : kinds(roots),
    outOfScope: new Set(doc?.out_of_scope ?? []),
    source: savedKinds ? "saved" : hints ? "provider" : "none",
  };
}

/** The document a save writes. `root_kinds: null` is written, not omitted, so
 *  "every kind" overrides a provider that defaults to fewer. */
export function viewDocFor(settings: {
  flattenKinds: Iterable<string>;
  rootKinds: Iterable<string> | null;
  outOfScope: Iterable<string>;
}): TreeViewDoc {
  return {
    schema: VIEW_DOC_SCHEMA,
    flatten_kinds: [...new Set([...settings.flattenKinds].map(normKind))].sort(),
    root_kinds: settings.rootKinds === null ? null : [...new Set([...settings.rootKinds].map(normKind))].sort(),
    out_of_scope: [...new Set(settings.outOfScope)].sort(),
    updated_at: new Date().toISOString(),
  };
}

/** Is `id`, or anything above it, out of scope? */
export function isOutOfScope<T>(h: Hierarchy<T>, outOfScope: ReadonlySet<string>, id: string): boolean {
  if (outOfScope.size === 0) return false;
  let cur: HierarchyNode<T> | undefined = h.byId.get(id);
  let guard = 0;
  while (cur && guard++ < 10_000) {
    if (outOfScope.has(cur.id)) return true;
    cur = cur.parent ? h.byId.get(cur.parent) : undefined;
  }
  return false;
}

export interface DisplayResult<T> {
  /** What `flattenVisible` walks: the same nodes, drawn through the settings. */
  readonly hierarchy: Hierarchy<T>;
  /** kind (as written) -> top-level branches of it, before the root filter --
   *  what the root-kind menu offers. */
  readonly rootKindCensus: ReadonlyMap<string, number>;
  /** Top-level branches the root filter hid. */
  readonly hiddenRoots: number;
  /** The root filter would have hidden everything, so it did nothing. */
  readonly rootFilterStoodDown: boolean;
}

/** The hierarchy as drawn: flattened kinds lifted out, out-of-scope branches
 *  removed unless `showHidden`, and the top level filtered by `rootKinds`
 *  unless a search is active. */
export function displayHierarchy<T extends { readonly kind: string }>(
  h: Hierarchy<T>,
  settings: TreeViewSettings,
  opts: {
    searchActive: boolean;
    showHidden: boolean;
    /** Rows to draw at all; a row it rejects is dropped with its subtree. The caller's filter --
     *  "is or contains something from this provider" -- applied at every level, so a kept branch
     *  shows only the way down to what matched. Absent: every row. */
    keep?: (id: string) => boolean;
  },
): DisplayResult<T> {
  const flatten = settings.flattenKinds;
  const hideOut = !opts.showHidden && settings.outOfScope.size > 0;
  const keep = opts.keep;
  const kindOf = (id: string) => normKind(h.byId.get(id)?.data.kind ?? "");

  // Children as drawn: a flattened child is replaced by its own drawn
  // children, recursively; an out-of-scope child is dropped with its subtree.
  const memo = new Map<string, readonly string[]>();
  const lift = (ids: readonly string[], depth: number): string[] => {
    const out: string[] = [];
    for (const id of ids) {
      if (hideOut && settings.outOfScope.has(id)) continue;
      if (keep && !keep(id)) continue;
      if (flatten.size && flatten.has(kindOf(id)) && depth < 64) out.push(...lift(h.childrenOf(id), depth + 1));
      else out.push(id);
    }
    return out;
  };
  const childrenOf = (id: string): readonly string[] => {
    let hit = memo.get(id);
    if (!hit) {
      hit = flatten.size || hideOut || keep ? lift(h.childrenOf(id), 0) : h.childrenOf(id);
      memo.set(id, hit);
    }
    return hit;
  };

  const lifted = flatten.size || hideOut || keep ? lift(h.roots, 0) : [...h.roots];
  const census = new Map<string, number>();
  for (const id of lifted) {
    const kind = (h.byId.get(id)?.data.kind ?? "").trim();
    census.set(kind, (census.get(kind) ?? 0) + 1);
  }

  let roots = lifted;
  let hiddenRoots = 0;
  let stoodDown = false;
  if (settings.rootKinds && !opts.searchActive) {
    const allowed = settings.rootKinds;
    const kept = lifted.filter((id) => allowed.has(kindOf(id)));
    if (kept.length === 0 && lifted.length > 0) stoodDown = true;
    else {
      hiddenRoots = lifted.length - kept.length;
      roots = kept;
    }
  }

  return {
    hierarchy: { byId: h.byId, childrenOf, roots, order: h.order },
    rootKindCensus: census,
    hiddenRoots,
    rootFilterStoodDown: stoodDown,
  };
}
