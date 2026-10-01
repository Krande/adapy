// The asset browser's own state. Deliberately NOT the tree view's store: the
// Files tab's store is eager, file-rooted and singular (one `treeData`, one
// range index), and the Assets tab is lazy, union-merged and derived. Sharing a
// store would make one of them lie about its shape.
//
// What lives here is INPUT -- the forest, the index, the mode, what is expanded
// and fetched. Everything a row shows is derived from these by
// `@/assets/assetView`, never stored.

import { create } from "zustand";

import type { SourceNodeRow, SourceNodesAnswer } from "@/assets/changes";
import type { LoadedAsset } from "@/assets/delivery";
import { EMPTY_FOREST, mergeLevel, mergeSpine, type Forest, type LevelMerge, type SpineMerge } from "@/assets/merge";
import type { TreeViewDoc, TreeViewHints } from "@/assets/treeView";
import type { AssetIndex, AssetNode, ResolutionMode } from "@/assets/types";

export type AssetBrowserTab = "files" | "assets";

/** How a tree row is drawn: `outline` (a glyph and the kind as a code) or
 *  `tiles` (a coloured tile per kind). A per-viewer preference, nothing more. */
export type AssetTreeStyle = "outline" | "tiles";

const TREE_STYLE_KEY = "ada.assets.treeStyle";

function readTreeStyle(): AssetTreeStyle {
  try {
    return globalThis.localStorage?.getItem(TREE_STYLE_KEY) === "tiles" ? "tiles" : "outline";
  } catch {
    return "outline"; // storage blocked (a private window, a sandbox): the default
  }
}

function writeTreeStyle(style: AssetTreeStyle): void {
  try {
    globalThis.localStorage?.setItem(TREE_STYLE_KEY, style);
  } catch {
    // Remembered for this page only; nothing depends on it surviving.
  }
}

const EMPTY_SET: ReadonlySet<string> = Object.freeze(new Set<string>());
const EMPTY_ERRORS: ReadonlyMap<string, string> = Object.freeze(new Map<string, string>());
const EMPTY_LOADED_ASSETS: readonly LoadedAsset[] = Object.freeze([]);
const EMPTY_SOURCE_ANSWERS: ReadonlyMap<string, SourceNodesAnswer | null> = Object.freeze(new Map());
const EMPTY_CHANGED_ROWS: ReadonlyMap<string, SourceNodeRow> = Object.freeze(new Map());

export interface AssetBrowserState {
  tab: AssetBrowserTab;

  /** The scope the data below came out of. A switch invalidates all of it. */
  scope: string | null;
  /** Collections in the scope, from the scope-wide index; null = not asked yet. */
  collections: readonly string[] | null;
  collection: string | null;
  /** The chosen collection's folded index, with manifest summaries. */
  index: AssetIndex | null;
  indexLoading: boolean;
  indexError: string | null;
  mode: ResolutionMode;

  /** Every row held, the spine that contributed each, and what was pruned. */
  forest: Forest;
  /** Bumped on every forest change, so a memo over a 41k-entry map keys on a
   *  number instead of re-scanning the map each render. */
  forestVersion: number;
  /** Collection-index revisions merged into the forest, ascending. */
  mergedIndexRevisions: readonly string[];
  /** LEVELS fetched, by `levelKey` (subject, revision, node): a spine is opened
   *  one level at a time -- expanding a row fetches that row's direct children
   *  from the spine that holds them, never the whole spine (one can be a whole
   *  site). By revision too, because a resolution change re-points a subject
   *  at another document. */
  levelLoaded: ReadonlySet<string>;
  levelLoading: ReadonlySet<string>;
  levelErrors: ReadonlyMap<string, string>;

  /** PROVIDER id -> the change feed's last answer for that provider, or
   *  `null` for that provider's own no-feed (§Decision 4's four states,
   *  `@/assets/changes`). Keyed by provider, not flattened, because a mixed
   *  collection can straddle providers with different feed availability --
   *  see `mergeSourceAnswer`. */
  sourceAnswer: ReadonlyMap<string, SourceNodesAnswer | null>;
  /** Every row across every provider whose `action` is non-null, flattened --
   *  the per-node evidence marks a row paints, kept pre-flattened so a render
   *  never re-scans every provider's answer. Recomputed by `mergeSourceAnswer`
   *  whenever a new answer comes in, from `sourceAnswer` in full (cheap: a
   *  sweep's rows are the changed nodes plus their ancestors, never the whole
   *  tree -- §Decision 4 -- so this map stays small regardless of forest size). */
  changedRows: ReadonlyMap<string, SourceNodeRow>;
  /** Every node ref (a root's own subject id, or any descendant) the tab has
   *  asked the feed about, across every provider -- the global dedup gate
   *  `assetBrowserLoader`'s evidence fetch reads before firing a request, and
   *  what tells `buildAssetView` "not yet asked" apart from "asked and got
   *  nothing back" (`not-recorded`). */
  evidenceAsked: ReadonlySet<string>;

  expanded: ReadonlySet<string>;
  /** The focused row. One field on purpose: the tree is virtualised over ~41k
   *  rows, so selection is never per-row state. */
  selected: string | null;
  /** Every selected row, `selected` among them: one, or a shift/ctrl-built range. Held as a set
   *  of ids rather than per-row state for the same reason. */
  selection: ReadonlySet<string>;
  /** Where a shift-range starts: the last row chosen by a plain or ctrl click. */
  anchor: string | null;
  searchTerm: string;

  /** How the provider suggests drawing this collection, off its newest merged
   *  collection index (`@/assets/treeView`). Null = no suggestion. */
  viewHints: TreeViewHints | null;
  /** What this scope saved for this collection; overrides `viewHints` field by
   *  field. Null = nothing saved (or not read yet). */
  viewDoc: TreeViewDoc | null;
  /** Draw out-of-scope branches anyway, marked. A browsing preference, so it
   *  survives a collection switch like the mode does. */
  showHidden: boolean;
  /** Row style, remembered per viewer. */
  treeStyle: AssetTreeStyle;

  /** Scene content loaded through the Assets tab (Phase 3), mirrored against
   *  the scene's live loaded-source set (`reconcileLoaded`) so a model
   *  unloaded elsewhere -- the Files tab, another plugin -- does not leave a
   *  stale "loaded" mark here. Not reset on a collection/scope switch: what
   *  is actually in the scene does not change just because the tab is
   *  looking somewhere else. */
  loaded: readonly LoadedAsset[];
  /** ROW id (the one the user clicked `Load` on, not necessarily the subject
   *  that owns the claim -- see `NodeRef`) -> a load in flight. */
  loadBusy: ReadonlySet<string>;
  /** ROW id -> why its last load attempt failed. Cleared on the next attempt. */
  loadErrors: ReadonlyMap<string, string>;

  setTab: (tab: AssetBrowserTab) => void;
  setScope: (scope: string | null) => void;
  setCollections: (collections: readonly string[]) => void;
  setCollection: (collection: string | null) => void;
  setIndex: (index: AssetIndex) => void;
  setIndexLoading: (loading: boolean) => void;
  setIndexError: (error: string | null) => void;
  /** Deliberately leaves the tree alone: a mode is a lens over the same
   *  hierarchy, and re-collapsing on every switch would make comparing two
   *  publishes -- the reason the modes exist -- unusable. */
  setMode: (mode: ResolutionMode) => void;
  mergeSlice: (nodes: readonly AssetNode[], merge: SpineMerge) => void;
  /** One level of a spine (`mergeLevel`): unions with the levels already held
   *  from the same subject @ revision. */
  mergeLevelSlice: (nodes: readonly AssetNode[], merge: LevelMerge) => void;
  setMergedIndexRevisions: (revisions: readonly string[]) => void;
  beginLevel: (key: string) => void;
  endLevel: (key: string) => void;
  failLevel: (key: string, error: string) => void;
  /** Fold one provider's answer to a batch of refs into the running picture.
   *  `askedRefs` is recorded in `evidenceAsked` REGARDLESS of whether `answer`
   *  is a real answer or `null` -- asking and being told no-feed is still
   *  having asked, and is exactly what must stop `not-recorded` (a claim
   *  about the feed) from being confused with "nobody has asked yet" (a fact
   *  about this browser tab). `answer: null` replaces this provider's slot in
   *  `sourceAnswer` with `null` outright rather than merging into whatever
   *  rows it may have held before: a provider that has just told us it has no
   *  database cannot simultaneously be trusted for rows fetched a moment
   *  earlier, and keeping them would let a `current` badge outlive the
   *  answer that justified it. */
  mergeSourceAnswer: (source: string, answer: SourceNodesAnswer | null, askedRefs: readonly string[]) => void;
  toggleExpanded: (id: string) => void;
  setExpanded: (id: string, on: boolean) => void;
  /** Select `id` alone, and make it the anchor. */
  select: (id: string | null) => void;
  /** Select exactly `ids`, focused on `focus`; the anchor stays where it was. */
  selectRange: (ids: readonly string[], focus: string) => void;
  /** Add `id` to the selection or take it out (ctrl/cmd-click); it becomes the anchor. */
  toggleSelected: (id: string) => void;
  setSearchTerm: (term: string) => void;
  setViewHints: (hints: TreeViewHints | null) => void;
  setViewDoc: (doc: TreeViewDoc | null) => void;
  setShowHidden: (on: boolean) => void;
  setTreeStyle: (style: AssetTreeStyle) => void;
  /** A load just started for `id` -- clears any previous error for it too, so
   *  retrying a failed row does not show a stale message beside the spinner. */
  beginLoad: (id: string) => void;
  /** A load for `id` finished. Adds `asset` to `loaded` unless a source of
   *  that name is already there (two rows covered by the same ancestor both
   *  finishing must not duplicate the entry). */
  endLoad: (id: string, asset: LoadedAsset) => void;
  failLoad: (id: string, error: string) => void;
  clearLoadError: (id: string) => void;
  /** Drop every `loaded` entry whose source name the scene no longer holds --
   *  what keeps this mirror honest against an unload that happened anywhere
   *  else. Driven from `useModelState.loadedSourceNames`, never guessed. */
  reconcileLoaded: (liveSourceNames: ReadonlySet<string>) => void;
  /** Drop everything drawn from the current collection, keep the choice of it.
   *  What Refresh does: a union cannot express deletion, so rebuilding from
   *  nothing is the one way to see a tree with nothing stale in it. */
  resetForest: () => void;
  /** A scope switch invalidates every datum; the tab and the mode survive, as
   *  preferences about how the user browses rather than facts about the data. */
  resetForScope: (scope: string | null) => void;
}

const FOREST_RESET = {
  forest: EMPTY_FOREST,
  mergedIndexRevisions: [] as readonly string[],
  levelLoaded: EMPTY_SET,
  levelLoading: EMPTY_SET,
  levelErrors: EMPTY_ERRORS,
  // The change feed is asked about refs from THIS forest; a different
  // collection or a rebuilt forest has different refs to ask about, so
  // nothing here would still mean anything -- reset with the rest.
  sourceAnswer: EMPTY_SOURCE_ANSWERS,
  changedRows: EMPTY_CHANGED_ROWS,
  evidenceAsked: EMPTY_SET,
  expanded: EMPTY_SET,
  selected: null,
  selection: EMPTY_SET,
  anchor: null,
  // Re-read with the index it rides on. The saved view (`viewDoc`) is NOT here:
  // it belongs to the choice of collection, and Refresh rebuilds the forest
  // without re-reading it.
  viewHints: null,
};

export const useAssetBrowserStore = create<AssetBrowserState>((set) => ({
  tab: "files",
  scope: null,
  collections: null,
  collection: null,
  index: null,
  indexLoading: false,
  indexError: null,
  mode: { kind: "latest" },
  forest: EMPTY_FOREST,
  forestVersion: 0,
  mergedIndexRevisions: [],
  levelLoaded: EMPTY_SET,
  levelLoading: EMPTY_SET,
  levelErrors: EMPTY_ERRORS,
  sourceAnswer: EMPTY_SOURCE_ANSWERS,
  changedRows: EMPTY_CHANGED_ROWS,
  evidenceAsked: EMPTY_SET,
  expanded: EMPTY_SET,
  selected: null,
  selection: EMPTY_SET,
  anchor: null,
  searchTerm: "",
  viewHints: null,
  viewDoc: null,
  showHidden: false,
  treeStyle: readTreeStyle(),
  loaded: EMPTY_LOADED_ASSETS,
  loadBusy: EMPTY_SET,
  loadErrors: EMPTY_ERRORS,

  setTab: (tab) => set({ tab }),
  setScope: (scope) => set({ scope }),
  setCollections: (collections) => set({ collections }),
  setCollection: (collection) =>
    set((s) =>
      s.collection === collection
        ? s
        : { collection, index: null, indexError: null, ...FOREST_RESET, viewDoc: null, forestVersion: s.forestVersion + 1 },
    ),
  setIndex: (index) => set({ index, indexError: null }),
  setIndexLoading: (indexLoading) => set({ indexLoading }),
  setIndexError: (indexError) => set({ indexError }),
  setMode: (mode) => set({ mode }),

  mergeSlice: (nodes, merge) =>
    set((s) => {
      const forest = mergeSpine(s.forest, nodes, merge);
      return forest === s.forest ? s : { forest, forestVersion: s.forestVersion + 1 };
    }),
  mergeLevelSlice: (nodes, merge) =>
    set((s) => {
      const forest = mergeLevel(s.forest, nodes, merge);
      return forest === s.forest ? s : { forest, forestVersion: s.forestVersion + 1 };
    }),
  setMergedIndexRevisions: (mergedIndexRevisions) => set({ mergedIndexRevisions }),

  beginLevel: (key) =>
    set((s) => {
      const loading = new Set(s.levelLoading);
      loading.add(key);
      const errors = new Map(s.levelErrors);
      errors.delete(key);
      return { levelLoading: loading, levelErrors: errors };
    }),
  endLevel: (key) =>
    set((s) => {
      const loading = new Set(s.levelLoading);
      loading.delete(key);
      const loaded = new Set(s.levelLoaded);
      loaded.add(key);
      return { levelLoading: loading, levelLoaded: loaded };
    }),
  failLevel: (key, error) =>
    set((s) => {
      const loading = new Set(s.levelLoading);
      loading.delete(key);
      const errors = new Map(s.levelErrors);
      errors.set(key, error);
      return { levelLoading: loading, levelErrors: errors };
    }),

  mergeSourceAnswer: (source, answer, askedRefs) =>
    set((s) => {
      const asked = new Set(s.evidenceAsked);
      for (const ref of askedRefs) asked.add(ref);
      const bySource = new Map(s.sourceAnswer);
      bySource.set(source, answer);
      // Re-flattened from every provider's answer rather than patched
      // incrementally: a provider going from a real answer to `null` (its
      // own no-feed) must make ITS rows disappear from `changedRows` too, and
      // patching would have to special-case that removal every call for a
      // map that stays small regardless (§Decision 4's roll-up keeps the
      // feed to changed nodes and their ancestors, never the whole tree).
      const changedRows = new Map<string, SourceNodeRow>();
      for (const a of bySource.values()) {
        if (!a) continue;
        for (const [ref, row] of a.rows) if (row.action) changedRows.set(ref, row);
      }
      return { evidenceAsked: asked, sourceAnswer: bySource, changedRows };
    }),

  toggleExpanded: (id) =>
    set((s) => {
      const next = new Set(s.expanded);
      if (!next.delete(id)) next.add(id);
      return { expanded: next };
    }),
  setExpanded: (id, on) =>
    set((s) => {
      if (s.expanded.has(id) === on) return s;
      const next = new Set(s.expanded);
      if (on) next.add(id);
      else next.delete(id);
      return { expanded: next };
    }),
  select: (selected) => set({ selected, selection: selected === null ? EMPTY_SET : new Set([selected]), anchor: selected }),
  selectRange: (ids, focus) => set({ selected: focus, selection: new Set(ids) }),
  toggleSelected: (id) =>
    set((s) => {
      const selection = new Set(s.selection);
      if (!selection.delete(id)) selection.add(id);
      // Focus stays on the clicked row even when it was just deselected, as in a file manager;
      // the detail then shows the row the click was about.
      return { selection, selected: id, anchor: id };
    }),
  setSearchTerm: (searchTerm) => set({ searchTerm }),
  setViewHints: (viewHints) => set({ viewHints }),
  setViewDoc: (viewDoc) => set({ viewDoc }),
  setShowHidden: (showHidden) => set({ showHidden }),
  setTreeStyle: (treeStyle) => {
    writeTreeStyle(treeStyle);
    set({ treeStyle });
  },

  beginLoad: (id) =>
    set((s) => {
      const busy = new Set(s.loadBusy);
      busy.add(id);
      if (!s.loadErrors.has(id)) return { loadBusy: busy };
      const errors = new Map(s.loadErrors);
      errors.delete(id);
      return { loadBusy: busy, loadErrors: errors };
    }),
  endLoad: (id, asset) =>
    set((s) => {
      const busy = new Set(s.loadBusy);
      busy.delete(id);
      const loaded = s.loaded.some((a) => a.sourceName === asset.sourceName) ? s.loaded : [...s.loaded, asset];
      return { loadBusy: busy, loaded };
    }),
  failLoad: (id, error) =>
    set((s) => {
      const busy = new Set(s.loadBusy);
      busy.delete(id);
      const errors = new Map(s.loadErrors);
      errors.set(id, error);
      return { loadBusy: busy, loadErrors: errors };
    }),
  clearLoadError: (id) =>
    set((s) => {
      if (!s.loadErrors.has(id)) return s;
      const errors = new Map(s.loadErrors);
      errors.delete(id);
      return { loadErrors: errors };
    }),
  reconcileLoaded: (liveSourceNames) =>
    set((s) => {
      const next = s.loaded.filter((a) => liveSourceNames.has(a.sourceName));
      return next.length === s.loaded.length ? s : { loaded: next };
    }),

  resetForest: () => set((s) => ({ ...FOREST_RESET, forestVersion: s.forestVersion + 1 })),
  resetForScope: (scope) =>
    set((s) => ({
      scope,
      collections: null,
      collection: null,
      index: null,
      indexLoading: false,
      indexError: null,
      ...FOREST_RESET,
      viewDoc: null,
      forestVersion: s.forestVersion + 1,
      searchTerm: "",
      // `loaded` is left alone: it mirrors the scene, which a scope switch's
      // own model-clear reconciles separately, not a fact about this forest.
      loadBusy: EMPTY_SET,
      loadErrors: EMPTY_ERRORS,
    })),
}));
