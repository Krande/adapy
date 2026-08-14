import { create } from "zustand";

// Toggle state for the Factorio-style type-icon overlay: a lightning bolt over
// electrical gear, P/T/⚙ disks over pumps/tanks/other equipment, fluid drops
// (blue water / black oil) + bolts along system runs, and a red "!" badge over
// equipment with unconnected input ports. The overlay is store-driven off the
// cellbuilder model, so it works while editing or viewing a procedural model.

interface TypeIconsState {
  enabled: boolean;
  showEquipment: boolean;
  showMedia: boolean;
  showMissing: boolean;
  toggle: () => void;
  setEnabled: (v: boolean) => void;
  setShowEquipment: (v: boolean) => void;
  setShowMedia: (v: boolean) => void;
  setShowMissing: (v: boolean) => void;
}

export const useTypeIconsStore = create<TypeIconsState>((set) => ({
  enabled: false,
  showEquipment: true,
  showMedia: true,
  showMissing: true,
  toggle: () => set((s) => ({ enabled: !s.enabled })),
  setEnabled: (enabled) => set({ enabled }),
  setShowEquipment: (showEquipment) => set({ showEquipment }),
  setShowMedia: (showMedia) => set({ showMedia }),
  setShowMissing: (showMissing) => set({ showMissing }),
}));
