// How the procedural builder draws what the build leaves out (a component whose INCLUDE is false, or
// whose structure is: utils/cellbuilder/include): "dimmed" (the default) draws it faint with dashed
// edges, "included" hides it, "all" draws it like the rest. The user's choice is remembered in this
// browser, as the view toggles are. No three.js here.

import {create} from "zustand";

export type IncludeView = "dimmed" | "included" | "all";

export const INCLUDE_VIEWS: readonly IncludeView[] = ["dimmed", "included", "all"];

const KEY = "ada:builder-include-view";

const read = (): IncludeView => {
    try {
        const v = window.localStorage.getItem(KEY);
        return v === "included" || v === "all" ? v : "dimmed";
    } catch {
        return "dimmed";
    }
};

interface IncludeViewState {
    view: IncludeView;
    setView(view: IncludeView): void;
}

export const useIncludeViewStore = create<IncludeViewState>((set) => ({
    view: typeof window === "undefined" ? "dimmed" : read(),
    setView: (view) => {
        try {
            if (view === "dimmed") window.localStorage.removeItem(KEY);
            else window.localStorage.setItem(KEY, view);
        } catch {
            // Private window or blocked storage: the choice lasts this page.
        }
        set({view});
    },
}));
