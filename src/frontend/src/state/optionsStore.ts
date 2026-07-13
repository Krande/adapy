import {create} from "zustand";

// Pure UI / behaviour flags. Side-effects (e.g. opening or tearing
// down the transport when enableWebsocket toggles) live in
// services/transport.ts so the store has no transport coupling.

export type OptionsState = {
    isOptionsVisible: boolean;
    showPerf: boolean;
    showEdges: boolean;
    // Show the "Mesh" tessellation-stats block inside the selection's Properties panel.
    showMeshStats: boolean;
    hideTessellationEdges: boolean;
    lockTranslation: boolean;
    enableWebsocket: boolean;
    enableNodeEditor: boolean;
    pointSize: number;
    pointSizeAbsolute: boolean;
    useGpuPointPicking: boolean;
    // Fit the camera to the whole model after each load (zoom-to-all). Default ON so a
    // freshly loaded model — and each geom cycled through in gallery mode — is framed
    // without a manual Shift+A. Off keeps the camera where it is across loads.
    autoFit: boolean;
    // Auto-convert uploaded source files to GLB on upload. Default OFF — an
    // upload shouldn't silently spend worker time / spawn a conversion the user
    // didn't ask for; they trigger conversion explicitly from the file row.
    autoConvertOnUpload: boolean;
    // Decimal places shown for the "Clicked at" coordinate row. Adjustable because a
    // small model (a few mm across) needs more decimals before the displayed position
    // changes at all between nearby clicks; a building-scale model wants fewer.
    clickedCoordDecimals: number;
    // Face-level picking (opt-in). When on, clicks resolve the exact source face region (STEP/IFC
    // #id) via the raycast path instead of the fast GPU object-pick. Only meaningful for GLBs that
    // carry face_ranges (the scene-info toggle is hidden otherwise).
    faceLevelPicking: boolean;
    // Runtime capability (not a user preference): the most recently loaded model carries per-face
    // regions. Gates the solid/faces toggle so it only appears when face picking can actually work.
    faceRegionsAvailable: boolean;

    setIsOptionsVisible: (value: boolean) => void;
    setShowPerf: (value: boolean) => void;
    setShowEdges: (value: boolean) => void;
    setShowMeshStats: (value: boolean) => void;
    setHideTessellationEdges: (value: boolean) => void;
    setLockTranslation: (value: boolean) => void;
    setEnableWebsocket: (value: boolean) => void;
    setEnableNodeEditor: (value: boolean) => void;
    setPointSize: (value: number) => void;
    setPointSizeAbsolute: (value: boolean) => void;
    setUseGpuPointPicking: (value: boolean) => void;
    setAutoFit: (value: boolean) => void;
    setAutoConvertOnUpload: (value: boolean) => void;
    setClickedCoordDecimals: (value: number) => void;
    setFaceLevelPicking: (value: boolean) => void;
    setFaceRegionsAvailable: (value: boolean) => void;
};

export const useOptionsStore = create<OptionsState>((set) => ({
    isOptionsVisible: false,
    showPerf: false,
    showEdges: true,
    showMeshStats: true,
    hideTessellationEdges: true,
    lockTranslation: false,
    enableWebsocket: true,
    enableNodeEditor: false,
    pointSize: 0.01,
    pointSizeAbsolute: true,
    useGpuPointPicking: true,
    autoFit: true,
    autoConvertOnUpload: false,
    clickedCoordDecimals: 3,
    faceLevelPicking: false,
    faceRegionsAvailable: false,

    setIsOptionsVisible: (v) => set({isOptionsVisible: v}),
    setShowPerf: (v) => set({showPerf: v}),
    setShowEdges: (v) => set({showEdges: v}),
    setShowMeshStats: (v) => set({showMeshStats: v}),
    setHideTessellationEdges: (v) => set({hideTessellationEdges: v}),
    setLockTranslation: (v) => set({lockTranslation: v}),
    setEnableWebsocket: (v) => set({enableWebsocket: v}),
    setEnableNodeEditor: (v) => set({enableNodeEditor: v}),
    setPointSize: (v) => set({pointSize: v}),
    setPointSizeAbsolute: (v) => set({pointSizeAbsolute: v}),
    setUseGpuPointPicking: (v) => set({useGpuPointPicking: v}),
    setAutoFit: (v) => set({autoFit: v}),
    setAutoConvertOnUpload: (v) => set({autoConvertOnUpload: v}),
    setClickedCoordDecimals: (v) => set({clickedCoordDecimals: v}),
    setFaceLevelPicking: (v) => set({faceLevelPicking: v}),
    setFaceRegionsAvailable: (v) => set({faceRegionsAvailable: v}),
}));
