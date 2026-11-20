// Whether the classic UI's mode helper is shown (`layout/ModeHelper`): off by default, the
// user's choice remembered in this browser through the store's `persist` middleware. Storage
// that is blocked or missing reads as off. No three.js here.

import {create} from "zustand";
import {persist} from "zustand/middleware";

import {legacyAwareStorage} from "./legacyPersist";

/** The localStorage entry the setting is kept under. */
export const MODE_HELPER_STORAGE_KEY = "ada:mode-helper";

interface ModeHelperState {
    on: boolean;
    setOn(on: boolean): void;
    toggle(): void;
}

type Kept = Pick<ModeHelperState, "on">;

export const useModeHelperStore = create<ModeHelperState>()(
    persist(
        (set, get) => ({
            on: false,
            setOn: (on) => set({on}),
            toggle: () => get().setOn(!get().on),
        }),
        {
            name: MODE_HELPER_STORAGE_KEY,
            version: 1,
            storage: legacyAwareStorage<Kept>(() => null),
            migrate: (kept) => kept as Kept,
            partialize: (s) => ({on: s.on}),
        },
    ),
);
