// How the Model panel (the procedural component list) arranges its rows: flat, or as
// the structure tree -- the user's choice remembered in this browser, like the ground
// grid. Which group rows are folded is session state. A viewer preference, never part
// of the document: it changes nothing that is built.

import {create} from "zustand";

import {DEFAULT_MODEL_VIEW, modelViewFromStored, type ModelView} from "@/utils/cellbuilder/modelTree";

const KEY = "ada:model-panel-view";

const read = (): ModelView => {
    try {
        return modelViewFromStored(window.localStorage.getItem(KEY));
    } catch {
        return DEFAULT_MODEL_VIEW;
    }
};

interface ModelPanelViewState {
    view: ModelView;
    /** Keys of the group rows folded shut (every row starts open). */
    collapsed: Record<string, true>;
    setView(view: ModelView): void;
    toggleCollapsed(key: string): void;
    setCollapsed(key: string, collapsed: boolean): void;
}

export const useModelPanelViewStore = create<ModelPanelViewState>((set) => ({
    view: typeof window === "undefined" ? DEFAULT_MODEL_VIEW : read(),
    collapsed: {},
    setView: (view) => {
        try {
            if (view === DEFAULT_MODEL_VIEW) window.localStorage.removeItem(KEY);
            else window.localStorage.setItem(KEY, view);
        } catch {
            // Private window or blocked storage: the choice lasts this page.
        }
        set({view});
    },
    toggleCollapsed: (key) =>
        set((s) => {
            const next = {...s.collapsed};
            if (next[key]) delete next[key];
            else next[key] = true;
            return {collapsed: next};
        }),
    setCollapsed: (key, collapsed) =>
        set((s) => {
            if (Boolean(s.collapsed[key]) === collapsed) return {};
            const next = {...s.collapsed};
            if (collapsed) next[key] = true;
            else delete next[key];
            return {collapsed: next};
        }),
}));
