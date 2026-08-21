import {create} from "zustand";
import {persist} from "zustand/middleware";

// Which rail tools this user has hidden.
//
// Stored as the hidden set rather than the visible one, deliberately: a tool added in a
// later release is not in anybody's saved list, and with a visible-set it would be
// invisible to every existing user — a new feature nobody can find, and no error anywhere
// to explain it. Hidden-set means new tools show up.

interface RailPrefsState {
    hidden: string[];
    toggleHidden: (id: string) => void;
    reset: () => void;
}

export const useRailPrefs = create<RailPrefsState>()(
    persist(
        (set, get) => ({
            hidden: [],
            toggleHidden: (id) =>
                set({
                    hidden: get().hidden.includes(id)
                        ? get().hidden.filter((x) => x !== id)
                        : [...get().hidden, id],
                }),
            reset: () => set({hidden: []}),
        }),
        {name: "ada:rail-prefs:v1"},
    ),
);

export const railHidden = () => useRailPrefs.getState().hidden;
export const resetRail = () => useRailPrefs.getState().reset();
export const railIsCustomised = () => useRailPrefs.getState().hidden.length > 0;
