// The layout store: the user's `LayoutPrefs` (active preset, one document per
// preset and context, layout settings) and the actions that change them through
// the pure reducers of `./reducers`.
//
// PERSISTENCE is behind the asynchronous `LayoutPersistence` interface, so a
// per-user preferences endpoint on the server and a browser-local store fit the
// same seam. Phase 1 wires none: the classic preset's core panels are all bound
// to their existing flags, so there is nothing of the layout's own to keep yet.
// `hydrate` loads from whichever backend a host passes; until then (and with
// none) the store holds the defaults in memory.
//
// A vanilla zustand store (no React), so the layout controller and `node --test`
// use it directly; React reads it with `useStore`.

import { createStore, type StoreApi } from "zustand/vanilla";

import { layoutDocKey, type LayoutDoc, type LayoutPersistence, type LayoutPrefs, type ZoneDef } from "./layout";
import type { PresetId } from "./panel";
import { emptyLayoutDoc, migrateLayoutDoc } from "./reducers";

export const LAYOUT_PREFS_VERSION = 1;

export interface LayoutStoreState {
  prefs: LayoutPrefs;
  /** True once `hydrate` has run (whatever it found). */
  hydrated: boolean;
  /** The document of `preset` (and `context`), created from the preset's zones on first use. */
  doc(preset: PresetId, zones: readonly ZoneDef[], context?: string): LayoutDoc;
  /** Replace a document by applying a reducer to it. */
  update(preset: PresetId, zones: readonly ZoneDef[], fn: (doc: LayoutDoc) => LayoutDoc, context?: string): void;
  /**
   * The document a user starts from in `preset` (and `context`) while nothing is
   * saved for it; `null` restores the empty one. Not persisted: a host sets it at
   * startup (a composed shell's default grid).
   */
  setStartingDoc(preset: PresetId, context: string | undefined, make: (() => LayoutDoc) | null): void;
  setActivePreset(preset: PresetId | undefined): void;
  setCustomisablePanels(on: boolean): void;
  /** Pin or unpin a panel in a composed shell (by shell id). */
  setPinned(shell: string, panel: string, pinned: boolean): void;
  /** Forget a saved document: the next read starts from the preset's (or shell's) starting one. */
  removeDoc(preset: PresetId, context?: string): void;
  /** Save (`docs`) or delete (null) a named workspace of a composed shell. */
  setWorkspace(shell: string, name: string, docs: Record<string, LayoutDoc> | null): void;
  /** Replace saved documents at once (loading a workspace). */
  putDocs(docs: Record<string, LayoutDoc>): void;
  /** Load the saved prefs and keep saving every change to `persistence`. */
  hydrate(persistence: LayoutPersistence): Promise<void>;
}

export function emptyLayoutPrefs(): LayoutPrefs {
  return { v: LAYOUT_PREFS_VERSION, docs: {} };
}

/** Saved prefs made safe to use: anything that is not a prefs object of this version starts over. */
export function migrateLayoutPrefs(raw: unknown): LayoutPrefs {
  if (!raw || typeof raw !== "object") return emptyLayoutPrefs();
  const r = raw as Partial<LayoutPrefs>;
  if (r.v !== LAYOUT_PREFS_VERSION || !r.docs || typeof r.docs !== "object") return emptyLayoutPrefs();
  const prefs: LayoutPrefs = { v: LAYOUT_PREFS_VERSION, docs: { ...r.docs } };
  if (typeof r.activePreset === "string") prefs.activePreset = r.activePreset;
  if (typeof r.customisablePanels === "boolean") prefs.customisablePanels = r.customisablePanels;
  if (r.pinned && typeof r.pinned === "object") {
    const pinned: Record<string, string[]> = {};
    for (const [shell, ids] of Object.entries(r.pinned)) {
      if (Array.isArray(ids)) pinned[shell] = [...new Set(ids.filter((x): x is string => typeof x === "string"))];
    }
    prefs.pinned = pinned;
  }
  // Workspaces are kept as stored; each document is made safe when it is loaded.
  if (r.workspaces && typeof r.workspaces === "object") prefs.workspaces = r.workspaces;
  return prefs;
}

/** Prefs held in memory only: what a host without a backend gets. */
export function memoryLayoutPersistence(initial: LayoutPrefs | null = null): LayoutPersistence & { saved: LayoutPrefs | null } {
  const p = {
    saved: initial,
    load: async () => p.saved,
    save: async (prefs: LayoutPrefs) => {
      p.saved = prefs;
    },
  };
  return p;
}

export function createLayoutStore(): StoreApi<LayoutStoreState> {
  let persistence: LayoutPersistence | null = null;
  const starting = new Map<string, () => LayoutDoc>();
  const store = createStore<LayoutStoreState>()((set, get) => {
    const save = () => {
      if (persistence) void persistence.save(get().prefs).catch((err) => console.warn("[layout] save failed", err));
    };
    const setPrefs = (prefs: LayoutPrefs) => {
      set({ prefs });
      save();
    };
    return {
      prefs: emptyLayoutPrefs(),
      hydrated: false,
      doc(preset, zones, context) {
        const key = layoutDocKey(preset, context);
        const stored = get().prefs.docs[key];
        // Stored documents pass through the migration, so a document saved by
        // another version, or for zones the preset no longer has, is usable.
        if (stored) return migrateLayoutDoc(stored, preset, zones, context);
        const make = starting.get(key);
        return make ? migrateLayoutDoc(make(), preset, zones, context) : emptyLayoutDoc(preset, zones, context);
      },
      setStartingDoc(preset, context, make) {
        const key = layoutDocKey(preset, context);
        if (make) starting.set(key, make);
        else starting.delete(key);
      },
      update(preset, zones, fn, context) {
        const key = layoutDocKey(preset, context);
        const before = get().doc(preset, zones, context);
        const after = fn(before);
        if (after === before && get().prefs.docs[key]) return;
        setPrefs({ ...get().prefs, docs: { ...get().prefs.docs, [key]: after } });
      },
      setActivePreset(preset) {
        const prefs = { ...get().prefs };
        if (preset) prefs.activePreset = preset;
        else delete prefs.activePreset;
        setPrefs(prefs);
      },
      setCustomisablePanels(on) {
        setPrefs({ ...get().prefs, customisablePanels: on });
      },
      removeDoc(preset, context) {
        const key = layoutDocKey(preset, context);
        if (!get().prefs.docs[key]) return;
        const docs = { ...get().prefs.docs };
        delete docs[key];
        setPrefs({ ...get().prefs, docs });
      },
      setWorkspace(shell, name, docs) {
        const prefs = get().prefs;
        const mine = { ...prefs.workspaces?.[shell] };
        if (docs) mine[name] = docs;
        else if (name in mine) delete mine[name];
        else return;
        setPrefs({ ...prefs, workspaces: { ...prefs.workspaces, [shell]: mine } });
      },
      putDocs(docs) {
        setPrefs({ ...get().prefs, docs: { ...get().prefs.docs, ...docs } });
      },
      setPinned(shell, panel, pinned) {
        const prefs = get().prefs;
        const now = prefs.pinned?.[shell] ?? [];
        if (now.includes(panel) === pinned) return;
        const next = pinned ? [...now, panel] : now.filter((p) => p !== panel);
        setPrefs({ ...prefs, pinned: { ...prefs.pinned, [shell]: next } });
      },
      async hydrate(backend) {
        persistence = null; // no saving back what is being loaded
        let loaded: LayoutPrefs | null = null;
        try {
          loaded = await backend.load();
        } catch (err) {
          console.warn("[layout] loading saved layout failed; using defaults", err);
        }
        set({ prefs: loaded ? migrateLayoutPrefs(loaded) : get().prefs, hydrated: true });
        persistence = backend;
      },
    };
  });
  return store;
}

/** The viewer's layout store. */
export const layoutStore = createLayoutStore();
