// How the Model panel (the procedural component list) arranges its rows: flat, or as
// the structure tree -- the user's choice remembered in this browser through the store's
// `persist` middleware (versioned), like the ground grid. Which group rows are folded is
// session state, not kept. A viewer preference, never part of the document: it changes
// nothing that is built.
//
// A shell reads and sets it through `useModelPanelView` (@/viewer-core/layout), which hands out
// the state and the three actions, not the store.

import {create} from "zustand";
import {persist} from "zustand/middleware";

import {DEFAULT_MODEL_VIEW, modelViewFromStored, type ModelView} from "@/utils/cellbuilder/modelTree";
import {legacyAwareStorage} from "./legacyPersist";

/** The localStorage entry the setting is kept under. */
export const MODEL_PANEL_VIEW_STORAGE_KEY = "ada:model-panel-view";

interface ModelPanelViewState {
    view: ModelView;
    /** Keys of the group rows folded shut (every row starts open). */
    collapsed: Record<string, true>;
    setView(view: ModelView): void;
    toggleCollapsed(key: string): void;
    setCollapsed(key: string, collapsed: boolean): void;
}

type Kept = Pick<ModelPanelViewState, "view">;

const asView = (v: unknown): ModelView => modelViewFromStored(typeof v === "string" ? v : null);

export const useModelPanelViewStore = create<ModelPanelViewState>()(
    persist(
        (set) => ({
            view: DEFAULT_MODEL_VIEW,
            collapsed: {},
            setView: (view) => set({view}),
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
        }),
        {
            name: MODEL_PANEL_VIEW_STORAGE_KEY,
            version: 1,
            // Before version 1 the key held the view's id, absent for the default.
            storage: legacyAwareStorage<Kept>((raw) => ({view: asView(raw)})),
            migrate: (kept) => ({view: asView((kept as Partial<Kept> | undefined)?.view)}),
            merge: (kept, now) => ({...now, view: asView((kept as Partial<Kept> | undefined)?.view)}),
            partialize: (s) => ({view: s.view}),
        },
    ),
);

/** The Model panel's view as a shell sees it: the state and its three actions, not the store. */
export interface ModelPanelView {
    view: ModelView;
    collapsed: Readonly<Record<string, true>>;
    setView(view: ModelView): void;
    toggleCollapsed(key: string): void;
    setCollapsed(key: string, collapsed: boolean): void;
}

/** The Model panel's view (flat or the structure tree, and the folded rows), re-rendering on change. */
export function useModelPanelView(): ModelPanelView {
    const view = useModelPanelViewStore((s) => s.view);
    const collapsed = useModelPanelViewStore((s) => s.collapsed);
    const {setView, toggleCollapsed, setCollapsed} = useModelPanelViewStore.getState();
    return {view, collapsed, setView, toggleCollapsed, setCollapsed};
}
