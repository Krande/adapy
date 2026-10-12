// Whether the attribute search window is open.
//
// Its own flag, not a slot of the in-viewer panel host (`viewerPanelStore`), which shows one panel
// at a time: the search must stay open beside Admin or Convert. What the search found lives in
// `attributeSearchStore`, so closing the window loses nothing.

import {create} from "zustand";

/** The window's title, and its launchers' label: it spans every collection of every provider. */
export const SEARCH_WINDOW_TITLE = "Search all collections (attributes)";

interface SearchWindowState {
    open: boolean;
    show: () => void;
    hide: () => void;
    toggle: () => void;
}

export const useSearchWindowStore = create<SearchWindowState>((set) => ({
    open: false,
    show: () => set({open: true}),
    hide: () => set({open: false}),
    toggle: () => set((s) => ({open: !s.open})),
}));
