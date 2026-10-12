// The attribute search panel's state: the form, the query last run from it, the hits fetched so
// far, and which of them are picked.
//
// A STORE RATHER THAN COMPONENT STATE so what the panel found outlives the panel (closed and opened
// again, the results are still there) and so other UI can act on it without a prop chain: an
// export of the hits reads `lastQuery` and `selectedHits()` from here, exactly what the panel
// shows, and re-runs `lastQuery` page by page when it wants every hit rather than the loaded ones.
//
// The form is remembered per browser (localStorage): the last query run, not results. Storage can
// be blocked (a private window, a sandbox) -- the panel then simply starts empty.

import { create } from "zustand";

import {
  buildQuery,
  emptyForm,
  hitKey,
  mergePage,
  parseStoredForm,
  type SearchForm,
  type SearchResults,
} from "@/assets/attributeSearch";
import { attributeSearchApi, type SearchHit, type SearchQuery } from "@/services/api/attributeSearch";

export type { SearchHit, SearchQuery } from "@/services/api/attributeSearch";

const FORM_KEY = "ada.assets.attributeSearch.form";

function readForm(): SearchForm {
  try {
    return parseStoredForm(globalThis.localStorage?.getItem(FORM_KEY) ?? null) ?? emptyForm();
  } catch {
    return emptyForm();
  }
}

function writeForm(form: SearchForm): void {
  try {
    globalThis.localStorage?.setItem(FORM_KEY, JSON.stringify(form));
  } catch {
    // Remembered for this page only.
  }
}

export interface AttributeSearchState {
  form: SearchForm;
  /** The scope the results below came from. */
  scope: string | null;
  /** The query the results below answer, cursor cleared -- what an export re-runs. */
  lastQuery: SearchQuery | null;
  results: SearchResults | null;
  busy: boolean;
  error: string | null;
  /** Picked hits, by `hitKey`. */
  selected: ReadonlySet<string>;

  setForm: (patch: Partial<SearchForm>) => void;
  resetForm: () => void;
  /** Run the form as a new query (page one). */
  run: (scope: string) => Promise<void>;
  /** Fetch the next page of the last query, if there is one. */
  loadMore: () => Promise<void>;
  toggleSelected: (key: string) => void;
  /** Pick every loaded hit, or none. */
  selectAll: (on: boolean) => void;
  /** The picked hits, in result order. */
  selectedHits: () => SearchHit[];
}

/** Bumped per run: a page arriving for an older run is dropped. */
let generation = 0;

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export const useAttributeSearchStore = create<AttributeSearchState>((set, get) => ({
  form: readForm(),
  scope: null,
  lastQuery: null,
  results: null,
  busy: false,
  error: null,
  selected: new Set(),

  setForm: (patch) => set((s) => ({ form: { ...s.form, ...patch } })),
  resetForm: () => {
    const form = emptyForm();
    writeForm(form);
    set({ form });
  },

  run: async (scope) => {
    const form = get().form;
    const built = buildQuery(form, null);
    if (!built.ok) {
      set({ error: built.error });
      return;
    }
    writeForm(form);
    const gen = ++generation;
    set({ busy: true, error: null, scope, lastQuery: built.query, results: null, selected: new Set() });
    try {
      const page = await attributeSearchApi.searchAssetAttributes(scope, built.query);
      if (gen !== generation) return;
      set({ results: mergePage(null, page), busy: false });
    } catch (e) {
      if (gen === generation) set({ busy: false, error: message(e) });
    }
  },

  loadMore: async () => {
    const { scope, lastQuery, results, busy } = get();
    if (!scope || !lastQuery || !results?.nextCursor || busy) return;
    const gen = generation;
    set({ busy: true, error: null });
    try {
      const page = await attributeSearchApi.searchAssetAttributes(scope, { ...lastQuery, cursor: results.nextCursor });
      if (gen !== generation) return;
      set((s) => ({ results: mergePage(s.results, page), busy: false }));
    } catch (e) {
      if (gen === generation) set({ busy: false, error: message(e) });
    }
  },

  toggleSelected: (key) =>
    set((s) => {
      const next = new Set(s.selected);
      if (!next.delete(key)) next.add(key);
      return { selected: next };
    }),
  selectAll: (on) => set((s) => ({ selected: on ? new Set((s.results?.hits ?? []).map(hitKey)) : new Set() })),
  selectedHits: () => {
    const { results, selected } = get();
    return (results?.hits ?? []).filter((h) => selected.has(hitKey(h)));
  },
}));
