import {create} from "zustand";

// Pure UI / behaviour flags. Side-effects (e.g. opening or tearing
// down the transport when enableWebsocket toggles) live in
// services/transport.ts so the store has no transport coupling.

export type OptionsState = {
    isOptionsVisible: boolean;
    showPerf: boolean;
    showEdges: boolean;
    hideTessellationEdges: boolean;
    lockTranslation: boolean;
    enableWebsocket: boolean;
    enableNodeEditor: boolean;
    pointSize: number;
    pointSizeAbsolute: boolean;
    useGpuPointPicking: boolean;

    setIsOptionsVisible: (value: boolean) => void;
    setShowPerf: (value: boolean) => void;
    setShowEdges: (value: boolean) => void;
    setHideTessellationEdges: (value: boolean) => void;
    setLockTranslation: (value: boolean) => void;
    setEnableWebsocket: (value: boolean) => void;
    setEnableNodeEditor: (value: boolean) => void;
    setPointSize: (value: number) => void;
    setPointSizeAbsolute: (value: boolean) => void;
    setUseGpuPointPicking: (value: boolean) => void;
};

export const useOptionsStore = create<OptionsState>((set) => ({
    isOptionsVisible: false,
    showPerf: false,
    showEdges: true,
    hideTessellationEdges: true,
    lockTranslation: false,
    enableWebsocket: true,
    enableNodeEditor: false,
    pointSize: 0.01,
    pointSizeAbsolute: true,
    useGpuPointPicking: true,

    setIsOptionsVisible: (v) => set({isOptionsVisible: v}),
    setShowPerf: (v) => set({showPerf: v}),
    setShowEdges: (v) => set({showEdges: v}),
    setHideTessellationEdges: (v) => set({hideTessellationEdges: v}),
    setLockTranslation: (v) => set({lockTranslation: v}),
    setEnableWebsocket: (v) => set({enableWebsocket: v}),
    setEnableNodeEditor: (v) => set({enableNodeEditor: v}),
    setPointSize: (v) => set({pointSize: v}),
    setPointSizeAbsolute: (v) => set({pointSizeAbsolute: v}),
    setUseGpuPointPicking: (v) => set({useGpuPointPicking: v}),
}));
