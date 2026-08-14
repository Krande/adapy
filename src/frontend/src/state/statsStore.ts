// Store for the viewer "Stats" panel (model quantity take-off).
//
// Holds the take-off fetched for the active model's compiled GLB, plus the
// detail-panel UI state (open? which tab?). Fetching is best-effort: a model
// with no sidecar (a capability engine / STEP-IFC imports) resolves to available:false and
// the card shows a muted "take-off not available" state rather than erroring.

import { create } from "zustand";

import { viewerApi } from "@/services/viewerApi";
import type { ModelStats, StatsTabKey } from "@/utils/stats/modelStats";

export interface StatsState {
  // What the current stats belong to (so the export endpoint can be reached).
  scope: string | null;
  modelId: string | null;
  derivedKey: string | null;

  loading: boolean;
  available: boolean;
  stats: ModelStats | null;

  // Detail-panel UI.
  detailOpen: boolean;
  activeTab: StatsTabKey;
  exportMenuOpen: boolean;
  exporting: boolean;

  fetchModelStats: (scope: string, modelId: string, derivedKey: string) => Promise<void>;
  clearStats: () => void;
  openDetail: () => void;
  closeDetail: () => void;
  setActiveTab: (tab: StatsTabKey) => void;
  setExportMenuOpen: (open: boolean) => void;
  exportStats: (fmt: "xlsx" | "csv") => Promise<void>;
}

export const useStatsStore = create<StatsState>((set, get) => ({
  scope: null,
  modelId: null,
  derivedKey: null,
  loading: false,
  available: false,
  stats: null,
  detailOpen: false,
  activeTab: "overview",
  exportMenuOpen: false,
  exporting: false,

  fetchModelStats: async (scope, modelId, derivedKey) => {
    if (!derivedKey) return;
    set({ loading: true, scope, modelId, derivedKey });
    try {
      const res = await viewerApi.fetchModelStats(scope, modelId, derivedKey);
      // Guard against a stale response after a newer fetch superseded us.
      if (get().derivedKey !== derivedKey) return;
      set({
        loading: false,
        available: Boolean(res.available && res.stats),
        stats: res.stats ?? null,
      });
    } catch {
      if (get().derivedKey !== derivedKey) return;
      set({ loading: false, available: false, stats: null });
    }
  },

  clearStats: () =>
    set({
      scope: null,
      modelId: null,
      derivedKey: null,
      loading: false,
      available: false,
      stats: null,
      detailOpen: false,
      exportMenuOpen: false,
    }),

  openDetail: () => set({ detailOpen: true }),
  closeDetail: () => set({ detailOpen: false, exportMenuOpen: false }),
  setActiveTab: (activeTab) => set({ activeTab }),
  setExportMenuOpen: (exportMenuOpen) => set({ exportMenuOpen }),

  exportStats: async (fmt) => {
    const { scope, modelId, derivedKey, activeTab } = get();
    if (!scope || !modelId || !derivedKey) return;
    set({ exporting: true, exportMenuOpen: false });
    try {
      await viewerApi.downloadStatsExport(scope, modelId, derivedKey, fmt, activeTab);
    } catch {
      // A failed export is non-fatal; leave the panel as-is.
    } finally {
      set({ exporting: false });
    }
  },
}));
