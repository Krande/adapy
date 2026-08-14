// Per-scope procedural-engine registry admin state (master/detail with an
// optimistic-revision draft), mirroring the equipment/system catalog stores.
// The built-in "adapy-default" engine is listed (origin "builtin") but is
// read-only — it has no editable draft.
import { create } from "zustand";

import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import {
  viewerApi,
  type ProceduralEngineDetail,
  type ProceduralEngineDoc,
  type ProceduralEngineSummary,
} from "@/services/viewerApi";

function scopePart(): string {
  const scope = useScopeStore.getState().current;
  return scope ? scopeUrlPart(scope) : "user:me";
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

const DEFAULT_DOC: ProceduralEngineDoc = {
  kind: "wheel",
  repo_url: null,
  ref: null,
  deploy_key_secret: null,
  entrypoint: null,
  pyodide_deps: [],
  wheel_key: null,
};

interface EngineCatalogState {
  engines: ProceduralEngineSummary[];
  selectedId: string | null;
  draft: ProceduralEngineDetail | null;
  dirty: boolean;
  busy: boolean;
  error: string | null;

  refresh: () => Promise<void>;
  create: (name: string) => Promise<void>;
  select: (id: string | null) => Promise<void>;
  setMeta: (patch: { name?: string; description?: string | null }) => void;
  setDoc: (patch: Partial<ProceduralEngineDoc>) => void;
  save: () => Promise<void>;
  remove: (id: string) => Promise<void>;
}

export const useEngineCatalogStore = create<EngineCatalogState>((set, get) => ({
  engines: [],
  selectedId: null,
  draft: null,
  dirty: false,
  busy: false,
  error: null,

  refresh: async () => {
    set({ busy: true, error: null });
    try {
      const engines = await viewerApi.listProceduralEngines(scopePart());
      set({ engines, busy: false });
    } catch (e) {
      set({ error: errMsg(e), busy: false });
    }
  },

  create: async (name) => {
    if (!name.trim()) return;
    set({ busy: true, error: null });
    try {
      const created = await viewerApi.createProceduralEngine(
        scopePart(),
        name.trim(),
      );
      await get().refresh();
      await get().select(created.id);
      set({ busy: false });
    } catch (e) {
      set({ error: errMsg(e), busy: false });
    }
  },

  select: async (id) => {
    if (id === null) {
      set({ selectedId: null, draft: null, dirty: false });
      return;
    }
    // The built-in engine is read-only — never open an editable draft for it.
    const summary = get().engines.find((e) => e.id === id);
    if (summary?.origin === "builtin") {
      set({ selectedId: id, draft: null, dirty: false });
      return;
    }
    set({ busy: true, error: null });
    try {
      const detail = await viewerApi.getProceduralEngine(scopePart(), id);
      set({ selectedId: id, draft: detail, dirty: false, busy: false });
    } catch (e) {
      set({ error: errMsg(e), busy: false });
    }
  },

  setMeta: (patch) => {
    const draft = get().draft;
    if (!draft) return;
    set({ draft: { ...draft, ...patch }, dirty: true });
  },

  setDoc: (patch) => {
    const draft = get().draft;
    if (!draft) return;
    set({
      draft: { ...draft, doc: { ...DEFAULT_DOC, ...draft.doc, ...patch } },
      dirty: true,
    });
  },

  save: async () => {
    const draft = get().draft;
    if (!draft) return;
    set({ busy: true, error: null });
    try {
      const { revision } = await viewerApi.updateProceduralEngine(
        scopePart(),
        draft.id,
        { name: draft.name, description: draft.description, doc: draft.doc },
        draft.revision,
      );
      set({ draft: { ...draft, revision }, dirty: false, busy: false });
      await get().refresh();
    } catch (e) {
      set({ error: errMsg(e), busy: false });
    }
  },

  remove: async (id) => {
    set({ busy: true, error: null });
    try {
      await viewerApi.deleteProceduralEngine(scopePart(), id);
      if (get().selectedId === id)
        set({ selectedId: null, draft: null, dirty: false });
      await get().refresh();
      set({ busy: false });
    } catch (e) {
      set({ error: errMsg(e), busy: false });
    }
  },
}));
