// Store for the viewer "Stats" panel (model quantity take-off).
//
// Holds the take-off for the active model, plus the detail-panel UI state
// (open? which tab?). Fetching is best-effort: a model with no take-off (a
// capability engine / STEP-IFC imports) resolves to available:false and the
// card shows a muted "take-off not available" state rather than erroring.
//
// Where the take-off comes from is NOT this store's business. It asks
// `capabilities.stats` (services/capabilities), which is the REST sidecar fetch
// in hosted mode and the copy embedded in the pushed GLB's
// `asset.extras.model_stats` on the local `assembly.show()` websocket path.
// Before that seam existed this store called `viewerApi` directly, which is why
// a locally shown model could never populate the panel — there was no server to
// ask, and no non-REST way to express the same capability.

import { create } from "zustand";

import { capabilities } from "@/services/capabilities";
import type { ModelStatsSource } from "@/services/capabilities";
import type { ModelStats, StatsTabKey } from "@/utils/stats/modelStats";

export interface StatsState {
  // What the current stats belong to. Populated on the REST path (it addresses
  // the sidecar + the export endpoint); all null on the websocket path, where
  // the take-off travels with the model.
  scope: string | null;
  modelId: string | null;
  derivedKey: string | null;

  loading: boolean;
  available: boolean;
  stats: ModelStats | null;
  // Whether a server-rendered xlsx/csv export is reachable for the current
  // source. False on the websocket path — there is no backend to build it.
  canExport: boolean;

  // Detail-panel UI.
  detailOpen: boolean;
  activeTab: StatsTabKey;
  exportMenuOpen: boolean;
  exporting: boolean;

  fetchModelStats: (scope: string, modelId: string, derivedKey: string) => Promise<void>;
  /** Re-ask the capability for the current source. The GLB loader calls this
   * after handing a newly loaded model's embedded take-off to the capability. */
  refreshStats: () => Promise<void>;
  clearStats: () => void;
  openDetail: () => void;
  closeDetail: () => void;
  setActiveTab: (tab: StatsTabKey) => void;
  setExportMenuOpen: (open: boolean) => void;
  exportStats: (fmt: "xlsx" | "csv") => Promise<void>;
}

export const useStatsStore = create<StatsState>((set, get) => {
  const load = async (source: ModelStatsSource) => {
    set({ loading: true });
    try {
      const res = await capabilities.stats.fetchStats(source);
      // Guard against a stale response after a newer fetch superseded us.
      if (get().derivedKey !== (source.derivedKey ?? null)) return;
      set({
        loading: false,
        available: Boolean(res.available && res.stats),
        stats: res.stats ?? null,
        canExport: capabilities.stats.canExport(source),
      });
    } catch {
      if (get().derivedKey !== (source.derivedKey ?? null)) return;
      set({ loading: false, available: false, stats: null, canExport: false });
    }
  };

  return {
    scope: null,
    modelId: null,
    derivedKey: null,
    loading: false,
    available: false,
    stats: null,
    canExport: false,
    detailOpen: false,
    activeTab: "overview",
    exportMenuOpen: false,
    exporting: false,

    fetchModelStats: async (scope, modelId, derivedKey) => {
      if (!derivedKey) return;
      set({ scope, modelId, derivedKey });
      await load({ scope, modelId, derivedKey });
    },

    refreshStats: async () => {
      const { scope, modelId, derivedKey } = get();
      await load({ scope, modelId, derivedKey });
    },

    clearStats: () =>
      set({
        scope: null,
        modelId: null,
        derivedKey: null,
        loading: false,
        available: false,
        stats: null,
        canExport: false,
        detailOpen: false,
        exportMenuOpen: false,
      }),

    openDetail: () => set({ detailOpen: true }),
    closeDetail: () => set({ detailOpen: false, exportMenuOpen: false }),
    setActiveTab: (activeTab) => set({ activeTab }),
    setExportMenuOpen: (exportMenuOpen) => set({ exportMenuOpen }),

    exportStats: async (fmt) => {
      const { scope, modelId, derivedKey, activeTab } = get();
      const source = { scope, modelId, derivedKey };
      if (!capabilities.stats.canExport(source)) return;
      set({ exporting: true, exportMenuOpen: false });
      try {
        await capabilities.stats.exportStats(source, fmt, activeTab);
      } catch {
        // A failed export is non-fatal; leave the panel as-is.
      } finally {
        set({ exporting: false });
      }
    },
  };
});
