/**
 * How the cellbuilder's GROUPS are SHOWN -- colour by group, per-group colours,
 * which groups are hidden. A viewer/session preference, deliberately NOT part of
 * the procedural document: it changes nothing that is built, so it must not
 * mark the model dirty, enter its undo history or ride along on a commit. (What
 * IS built -- a group's blueprint and whether it is included -- lives on the
 * group in the document; see `utils/cellbuilder/groups.ts`.)
 *
 * The scene side that applies this state to the cell boxes and the compiled
 * result is `utils/cellbuilder/groupView.ts`.
 */

import {create} from "zustand";

interface GroupViewState {
    /** Tint the cell boxes and the compiled structures by group. */
    colourByGroup: boolean;
    /** Per-model colour overrides: modelId -> group name -> "#rrggbb". A group
     *  without one draws with its palette colour (`groupColor`). */
    colours: Record<string, Record<string, string>>;
    /** Groups whose cells and compiled structure are hidden (the eye). */
    hiddenGroups: string[];
    setColourByGroup: (on: boolean) => void;
    setGroupColour: (modelId: string, name: string, colour: string) => void;
    setHiddenGroups: (names: string[]) => void;
    /** Carry a group's colour and hidden state across a rename. */
    renameGroup: (modelId: string, from: string, to: string) => void;
}

export const useGroupViewStore = create<GroupViewState>()((set) => ({
    colourByGroup: false,
    colours: {},
    hiddenGroups: [],
    setColourByGroup: (colourByGroup) => set({colourByGroup}),
    setGroupColour: (modelId, name, colour) =>
        set((s) => ({colours: {...s.colours, [modelId]: {...(s.colours[modelId] ?? {}), [name]: colour}}})),
    setHiddenGroups: (hiddenGroups) => set({hiddenGroups}),
    renameGroup: (modelId, from, to) =>
        set((s) => {
            const mine = {...(s.colours[modelId] ?? {})};
            if (from in mine) {
                mine[to] = mine[from];
                delete mine[from];
            }
            return {
                colours: {...s.colours, [modelId]: mine},
                hiddenGroups: s.hiddenGroups.map((g) => (g === from ? to : g)),
            };
        }),
}));

/** The colour overrides of model `modelId` (stable empty object when none). */
const NONE: Record<string, string> = {};
export const coloursOf = (s: {colours: Record<string, Record<string, string>>}, modelId: string | null | undefined) =>
    (modelId && s.colours[modelId]) || NONE;
