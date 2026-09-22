// The asset browser's own state. Deliberately NOT the tree view's store: the
// Files tab's store is eager, file-rooted and singular (one `treeData`, one
// range index), and the Assets tab is lazy, union-merged and derived. Sharing a
// store would make one of them lie about its shape.
//
// What lives here is INPUT -- the forest, the index, the mode, what is expanded
// and fetched. Everything a row shows is derived from these by
// `@/assets/assetView`, never stored.

import { create } from "zustand";

import type { LoadedAsset } from "@/assets/delivery";
import { EMPTY_FOREST, mergeSpine, type Forest, type SpineMerge } from "@/assets/merge";
import type { AssetIndex, AssetNode, ResolutionMode } from "@/assets/types";

export type AssetBrowserTab = "files" | "assets";

const EMPTY_SET: ReadonlySet<string> = Object.freeze(new Set<string>());
const EMPTY_LOADED: ReadonlyMap<string, string> = Object.freeze(new Map<string, string>());
const EMPTY_ERRORS: ReadonlyMap<string, string> = Object.freeze(new Map<string, string>());
const EMPTY_LOADED_ASSETS: readonly LoadedAsset[] = Object.freeze([]);

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
  /** SPINE ROOT -> the revision it was fetched at. Keyed by the spine, not the
   *  row that asked, so a second branch under one spine does not re-fetch it;
   *  and by revision, because a resolution change re-points the spine. */
  spineLoaded: ReadonlyMap<string, string>;
  spineLoading: ReadonlySet<string>;
  spineErrors: ReadonlyMap<string, string>;

  expanded: ReadonlySet<string>;
  /** The focused row. One field on purpose: the tree is virtualised over ~41k
   *  rows, so selection is never per-row state. */
  selected: string | null;
  searchTerm: string;

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
  setMergedIndexRevisions: (revisions: readonly string[]) => void;
  beginSpine: (root: string) => void;
  endSpine: (root: string, revision: string) => void;
  failSpine: (root: string, error: string) => void;
  toggleExpanded: (id: string) => void;
  setExpanded: (id: string, on: boolean) => void;
  select: (id: string | null) => void;
  setSearchTerm: (term: string) => void;
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
  spineLoaded: EMPTY_LOADED,
  spineLoading: EMPTY_SET,
  spineErrors: EMPTY_ERRORS,
  expanded: EMPTY_SET,
  selected: null,
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
  spineLoaded: EMPTY_LOADED,
  spineLoading: EMPTY_SET,
  spineErrors: EMPTY_ERRORS,
  expanded: EMPTY_SET,
  selected: null,
  searchTerm: "",
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
        : { collection, index: null, indexError: null, ...FOREST_RESET, forestVersion: s.forestVersion + 1 },
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
  setMergedIndexRevisions: (mergedIndexRevisions) => set({ mergedIndexRevisions }),

  beginSpine: (root) =>
    set((s) => {
      const loading = new Set(s.spineLoading);
      loading.add(root);
      const errors = new Map(s.spineErrors);
      errors.delete(root);
      return { spineLoading: loading, spineErrors: errors };
    }),
  endSpine: (root, revision) =>
    set((s) => {
      const loading = new Set(s.spineLoading);
      loading.delete(root);
      const loaded = new Map(s.spineLoaded);
      loaded.set(root, revision);
      return { spineLoading: loading, spineLoaded: loaded };
    }),
  failSpine: (root, error) =>
    set((s) => {
      const loading = new Set(s.spineLoading);
      loading.delete(root);
      const errors = new Map(s.spineErrors);
      errors.set(root, error);
      return { spineLoading: loading, spineErrors: errors };
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
  select: (selected) => set({ selected }),
  setSearchTerm: (searchTerm) => set({ searchTerm }),

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
      forestVersion: s.forestVersion + 1,
      searchTerm: "",
      // `loaded` is left alone: it mirrors the scene, which a scope switch's
      // own model-clear reconciles separately, not a fact about this forest.
      loadBusy: EMPTY_SET,
      loadErrors: EMPTY_ERRORS,
    })),
}));
