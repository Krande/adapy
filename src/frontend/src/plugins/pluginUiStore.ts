import { create } from "zustand";

// Per-panel visibility for plugin panels that expose a top-bar button. A button
// toggles its panel's key here; the panel renders while the key is truthy.
// Panels WITHOUT a top-bar button aren't tracked here — they render whenever
// their activation predicate is satisfied (the FEM-sidebar / data-present case).
interface PluginUiState {
  visible: Record<string, boolean>;
  toggle: (panelId: string) => void;
  setVisible: (panelId: string, v: boolean) => void;
  isVisible: (panelId: string) => boolean;
}

export const usePluginUiStore = create<PluginUiState>((set, get) => ({
  visible: {},
  toggle: (panelId) =>
    set((s) => ({ visible: { ...s.visible, [panelId]: !s.visible[panelId] } })),
  setVisible: (panelId, v) =>
    set((s) => ({ visible: { ...s.visible, [panelId]: v } })),
  isVisible: (panelId) => !!get().visible[panelId],
}));
