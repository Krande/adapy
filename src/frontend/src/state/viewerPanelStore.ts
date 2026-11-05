// In-viewer modal-host state. Lets the options drawer (or any other
// trigger inside the main viewer page) open the Admin or Convert
// panel without leaving the 3D model. The dedicated path-mounted
// /admin and /convert routes still exist for direct URL navigation;
// the modal mode is just the click-through-from-viewer path.

import {create} from "zustand";

import {AdminTabDeepLink} from "./adminPanelStore";

/** One at a time by default. The attribute search is not one of these: it has its own window
 *  (`searchWindowStore`), so it can stay open beside them. */
export type ViewerPanel = "admin" | "convert";

type ViewerPanelState = {
    /** The window opened last (the one in front), or null when none is open. */
    open: ViewerPanel | null;
    /** Every open window, the one in front last. At most one while `exclusive`. */
    shown: ViewerPanel[];
    /** One window at a time: opening one closes the other. The layout turns it off where both may float. */
    exclusive: boolean;
    // When opening the admin panel, the tab to land on (e.g. "audit_runs" from the audit-sweep
    // toast). null = the panel's default tab. Embedded mode doesn't touch the URL hash, so this
    // is how a trigger deep-links a tab into the floating panel.
    adminTab: AdminTabDeepLink | null;
    openPanel: (p: ViewerPanel, adminTab?: AdminTabDeepLink) => void;
    /** Close `p`, or every window without an argument. */
    closePanel: (p?: ViewerPanel) => void;
    setExclusive: (on: boolean) => void;
};

export const useViewerPanelStore = create<ViewerPanelState>((set) => ({
    open: null,
    shown: [],
    exclusive: true,
    adminTab: null,
    openPanel: (p, adminTab) =>
        set((s) =>
            s.exclusive
                ? {open: p, shown: [p], adminTab: adminTab ?? null}
                : {
                      open: p,
                      shown: [...s.shown.filter((x) => x !== p), p],
                      // Opening the other window leaves the admin tab alone.
                      adminTab: p === "admin" ? (adminTab ?? null) : s.adminTab,
                  },
        ),
    closePanel: (p) =>
        set((s) => {
            // Not a panel (e.g. a click event, from `onClick={closePanel}`): close every window.
            if (typeof p !== "string" || (s.shown.length <= 1 && s.shown.includes(p))) return {open: null, shown: [], adminTab: null};
            if (!s.shown.includes(p)) return {};
            const shown = s.shown.filter((x) => x !== p);
            return {open: shown[shown.length - 1] ?? null, shown, adminTab: p === "admin" ? null : s.adminTab};
        }),
    setExclusive: (on) =>
        set((s) => {
            if (s.exclusive === on) return {};
            // Back to one at a time: the one in front stays.
            return on && s.shown.length > 1 ? {exclusive: on, shown: s.open ? [s.open] : []} : {exclusive: on};
        }),
}));
