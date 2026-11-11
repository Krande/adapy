// Whether the Layers panel (`core:layers`) is open. The layers themselves are in
// state/layerRegistry.ts; this is only the panel's flag, as each core panel has one.
import {create} from "zustand";

interface LayersPanelState {
    panelOpen: boolean;
    setPanelOpen: (open: boolean) => void;
}

export const useLayersPanelStore = create<LayersPanelState>((set) => ({
    panelOpen: false,
    setPanelOpen: (panelOpen) => set({panelOpen}),
}));
