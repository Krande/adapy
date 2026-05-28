// In-viewer modal-host state. Lets the options drawer (or any other
// trigger inside the main viewer page) open the Admin or Convert
// panel without leaving the 3D model. The dedicated path-mounted
// /admin and /convert routes still exist for direct URL navigation;
// the modal mode is just the click-through-from-viewer path.

import {create} from "zustand";

export type ViewerPanel = "admin" | "convert";

type ViewerPanelState = {
    open: ViewerPanel | null;
    openPanel: (p: ViewerPanel) => void;
    closePanel: () => void;
};

export const useViewerPanelStore = create<ViewerPanelState>((set) => ({
    open: null,
    openPanel: (p) => set({open: p}),
    closePanel: () => set({open: null}),
}));
