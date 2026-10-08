// The GEOMETRY overlay of the asset tree: for each row, is there something to LOAD -- here, over it,
// or somewhere below it -- or only tree? Pure: a function of the view, so the rows and the legend
// read one answer.
//
// "Geometry" is a publish whose delivery is `mesh` or `build` (a GLB to fetch, or one a provider
// builds on request); a `none` delivery is a tree with nothing to load. Any provider counts: the
// question is whether this row can be put in the scene at all.
//
// TWO SOURCES. The tree arrives a level at a time, so what the LOADED rows can say stops at the
// first unopened branch -- everything under it is `unknown`. The server's ROLL-UP
// (`GET /assets/geometry/{collection}`) answers over the whole collection tree: which subjects
// carry geometry, and every row with one below it. When it is present (and the view resolves
// `latest`, the one mode it answers), an unopened row it does not name is definitively `tree`.

import type { AssetView } from "./assetView";
import { ancestorsOf } from "./hierarchy";
import { rowFacts } from "./rowFacts";
import type { ResolutionMode, WireGeometryRollup } from "./types";

/** What the geometry overlay says about one row.
 *  - `here`      geometry is published AT this row;
 *  - `covered`   a publish ABOVE it carries geometry that includes it;
 *  - `below`     somewhere UNDER it is a publish with geometry -- the "contains geometry" answer;
 *  - `tree`      it is in a published tree, but nothing at, over or under it has geometry;
 *  - `unknown`   levels below are not fetched yet, so "below" cannot be ruled out. */
export type GeometryMark = "here" | "covered" | "below" | "tree" | "unknown";

/** Where the marks come from: the server's roll-up over the whole tree, or the loaded rows only. */
export type GeometrySource = "server" | "client";

export interface GeometryIndex {
  /** Subjects (row ids) whose resolved content, from any provider, carries geometry. */
  readonly at: ReadonlySet<string>;
  /** Rows with such a subject strictly below them. */
  readonly below: ReadonlySet<string>;
  /** `server` when the roll-up was folded in: then `at`/`below` cover the whole collection tree,
   *  and a row in neither, not covered, is tree-only even when its levels are unfetched. */
  readonly source: GeometrySource;
}

const LOADABLE = new Set(["mesh", "build"]);

/** Does this publish have something to LOAD? A `mesh`/`build` delivery -- unless its own manifest
 *  says the subtree holds no leaf, in which case a build can only fail ("names no leaf ... nothing
 *  to read"). Such a claim reads as tree-only, so nothing offers to load it. */
export function deliversGeometry(manifest: { delivery: string; leaves?: number | null } | null | undefined): boolean {
  return !!manifest && LOADABLE.has(manifest.delivery) && manifest.leaves !== 0;
}

/** Whether the roll-up speaks for what is on screen: the same collection, and the `latest`
 *  resolution -- the one the server computes. Under `as-of`/`run` the marks stay client-only. */
export function rollupApplies(
  rollup: WireGeometryRollup | null | undefined,
  collection: string | null,
  mode: ResolutionMode,
): rollup is WireGeometryRollup {
  return !!rollup && !!rollup.any && rollup.collection === collection && mode.kind === "latest";
}

/** The roll-up's section for `provider` (or `any`), when it applies to this view. */
export function rollupSection(
  view: AssetView,
  rollup: WireGeometryRollup | null | undefined,
  provider?: string,
): { readonly here: readonly string[]; readonly below: readonly string[] } | null {
  if (!rollupApplies(rollup, view.collection, view.resolution.mode)) return null;
  if (provider === undefined) return rollup.any ?? null;
  // A provider the roll-up does not list published no geometry anywhere in the collection.
  return rollup.providers?.[provider] ?? { here: [], below: [] };
}

/** Where geometry is published -- by any provider, or only by `provider` when given. A tree-only
 *  publish (delivery `none`) never counts: a provider that published the whole collection's TREE at
 *  its root would otherwise "cover" every row, and a filter on it would match everything.
 *
 *  `rollup` (the server's, when it was fetched) is folded in: its `here` joins `at` and its `below`
 *  joins `below`, so a branch nobody has opened is still marked. */
export function geometryIndex(
  view: AssetView,
  provider?: string,
  rollup?: WireGeometryRollup | null,
): GeometryIndex {
  const at = new Set<string>();
  for (const [subject, resolved] of view.resolution.subjects) {
    const contents =
      provider === undefined ? [resolved.content, ...resolved.byProvider.values()] : [resolved.byProvider.get(provider)];
    if (contents.some((c) => deliversGeometry(c?.manifest))) at.add(subject);
  }
  const below = new Set<string>();
  for (const subject of at) {
    if (!view.hierarchy.byId.has(subject)) continue;
    for (const a of ancestorsOf(view.hierarchy, subject)) below.add(a);
  }
  const section = rollupSection(view, rollup, provider);
  if (!section) return { at, below, source: "client" };
  for (const s of section.here) at.add(s);
  for (const b of section.below) below.add(b);
  return { at, below, source: "server" };
}

/** Whether row `id` IS or CONTAINS geometry from the index's provider(s): published at it, over it,
 *  or below it. Without the roll-up a row whose levels below are unfetched is kept (`unknown`) -- it
 *  may hold a match; with it, that row is decided like any other. */
export function rowHasGeometry(view: AssetView, idx: GeometryIndex, id: string): boolean {
  // Decided without `rowFacts`: the filter runs per drawn row, and "tree only" vs "nothing" -- the
  // one thing rowFacts would add -- is a rejection either way.
  if (idx.at.has(id) || idx.below.has(id)) return true;
  if (idx.source === "client" && view.unexplored.has(id)) return true;
  for (const a of ancestorsOf(view.hierarchy, id)) if (idx.at.has(a)) return true;
  return false;
}

/** Can row `id` itself be LOADED from what `idx` covers: geometry at it, or at a row above it.
 *  Stricter than `rowHasGeometry`, which also passes a row with geometry somewhere below it --
 *  a filter keeps such a row, but loading it loads nothing. */
export function rowLoadable(view: AssetView, idx: GeometryIndex, id: string): boolean {
  if (idx.at.has(id)) return true;
  for (const a of ancestorsOf(view.hierarchy, id)) if (idx.at.has(a)) return true;
  return false;
}

/** The mark for row `id`, or null for a row nothing was published at, over or under. */
export function geometryMark(view: AssetView, idx: GeometryIndex, id: string): GeometryMark | null {
  if (idx.at.has(id)) return "here";
  for (const a of ancestorsOf(view.hierarchy, id)) if (idx.at.has(a)) return "covered";
  if (idx.below.has(id)) return "below";
  if (view.unexplored.has(id)) return idx.source === "server" ? "tree" : "unknown";
  return (rowFacts(view, id)?.claims.length ?? 0) > 0 ? "tree" : null;
}

export const GEOMETRY_MARK_TITLE: Record<GeometryMark, string> = {
  here: "Geometry is published at this node -- it can be loaded",
  covered: "Covered by geometry published above -- it can be loaded",
  below: "Contains geometry: something below this node has geometry published",
  tree: "Tree only: nothing at, above or below this node has geometry to load",
  unknown: "Levels below are not fetched yet -- whether they hold geometry is not known",
};

export const GEOMETRY_SOURCE_TITLE: Record<GeometrySource, string> = {
  server: "Marks from the server's roll-up over the whole collection tree -- unopened branches are decided too",
  client: "Marks from the loaded rows only -- unopened branches stay unknown until expanded",
};
