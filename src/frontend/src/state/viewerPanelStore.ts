// In-viewer modal-host state. Lets the options drawer (or any other
// trigger inside the main viewer page) open the Admin or Convert
// panel without leaving the 3D model. The dedicated path-mounted
// /admin and /convert routes still exist for direct URL navigation;
// the modal mode is just the click-through-from-viewer path.

import {create} from "zustand";

import {AdminTab} from "./adminPanelStore";

export type ViewerPanel = "admin" | "convert";

type ViewerPanelState = {
    open: ViewerPanel | null;
    // When opening the admin panel, the tab to land on (e.g. "audit_runs" from the audit-sweep
    // toast). null = the panel's default tab. Embedded mode doesn't touch the URL hash, so this
    // is how a trigger deep-links a tab into the floating panel.
    adminTab: AdminTab | null;
    openPanel: (p: ViewerPanel, adminTab?: AdminTab) => void;
    closePanel: () => void;
};

export const useViewerPanelStore = create<ViewerPanelState>((set) => ({
    open: null,
    adminTab: null,
    openPanel: (p, adminTab) => set({open: p, adminTab: adminTab ?? null}),
    closePanel: () => set({open: null, adminTab: null}),
}));
