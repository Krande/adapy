// How the procedural builder draws what the build leaves out (a component whose INCLUDE is false, or
// whose structure is: utils/cellbuilder/include): "dimmed" (the default) draws it faint with dashed
// edges, "included" hides it, "all" draws it like the rest. The user's choice is remembered in this
// browser through the store's `persist` middleware (versioned), as the view toggles are. No three.js here.

import {create} from "zustand";
import {persist} from "zustand/middleware";

import {legacyAwareStorage} from "./legacyPersist";

export type IncludeView = "dimmed" | "included" | "all";

export const INCLUDE_VIEWS: readonly IncludeView[] = ["dimmed", "included", "all"];

/** The localStorage entry the setting is kept under. */
export const INCLUDE_VIEW_STORAGE_KEY = "ada:builder-include-view";

const asView = (v: unknown): IncludeView => (v === "included" || v === "all" ? v : "dimmed");

interface IncludeViewState {
    view: IncludeView;
    setView(view: IncludeView): void;
}

type Kept = Pick<IncludeViewState, "view">;

export const useIncludeViewStore = create<IncludeViewState>()(
    persist(
        (set) => ({
            view: "dimmed",
            setView: (view) => set({view}),
        }),
        {
            name: INCLUDE_VIEW_STORAGE_KEY,
            version: 1,
            // Before version 1 the key held the view's name, absent for "dimmed".
            storage: legacyAwareStorage<Kept>((raw) => ({view: asView(raw)})),
            migrate: (kept) => ({view: asView((kept as Partial<Kept> | undefined)?.view)}),
            merge: (kept, now) => ({...now, view: asView((kept as Partial<Kept> | undefined)?.view)}),
            partialize: (s) => ({view: s.view}),
        },
    ),
);
