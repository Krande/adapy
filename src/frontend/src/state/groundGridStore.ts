// Whether the 3D view draws the ground grid (viewport/api/groundGrid): shown by default, the
// user's choice remembered in this browser through the store's `persist` middleware (versioned, as
// the projection is). Both viewports follow it. No three.js here.

import {create} from "zustand";
import {persist} from "zustand/middleware";

import {legacyAwareStorage} from "./legacyPersist";

/** The localStorage entry the setting is kept under. */
export const GROUND_GRID_STORAGE_KEY = "ada:ground-grid";

interface GroundGridState {
    visible: boolean;
    setVisible(on: boolean): void;
    toggle(): void;
}

type Kept = Pick<GroundGridState, "visible">;

export const useGroundGridStore = create<GroundGridState>()(
    persist(
        (set, get) => ({
            visible: true,
            setVisible: (on) => set({visible: on}),
            toggle: () => get().setVisible(!get().visible),
        }),
        {
            name: GROUND_GRID_STORAGE_KEY,
            version: 1,
            // Before version 1 the key held "off" when the grid was hidden.
            storage: legacyAwareStorage<Kept>((raw) => ({visible: raw !== "off"})),
            migrate: (kept) => kept as Kept,
            partialize: (s) => ({visible: s.visible}),
        },
    ),
);
