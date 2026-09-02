import {create} from "zustand";

import type {BuilderCell} from "@/state/cellBuilderStore";

// Procedural models shown in the scene ALONGSIDE the one being edited.
//
// The cellbuilder edits exactly one document — gizmos, selection, undo/redo and
// the whole panel are built around that, and it is a real constraint rather
// than an arbitrary one. But nothing required the SCENE to hold only one model,
// and comparing two is an obvious thing to want.
//
// So a companion is a model that is present but not editable: its cells are
// drawn as plain boxes with no picking, no gizmo and no selection, or its
// compiled result is loaded as an ordinary scene source. Promoting one to
// active is a separate act (the kebab's "Make active"), and the cellbuilder
// stays exactly as it was.
//
// Deliberately its OWN store rather than a slice of cellBuilderStore: that
// store is the editing model and is already large, and a companion shares none
// of its machinery. Keeping them apart means a bug here cannot reach the
// editing path.

/** Which representation a companion shows.
 *
 * ``topology`` is drawn from its cells by CellBuilderController; the other two
 * are compiled GLBs loaded as normal scene sources. A companion that has never
 * compiled can only show topology, which is also the only representation that
 * exists before a first compile. */
export type CompanionRep = "topology" | "simulation" | "detail";

export interface CompanionModel {
    modelId: string;
    /** The model's full name — also its path, and its scene-source key. */
    name: string;
    /** Cells for the read-only topology drawing. Empty for a model with none. */
    cells: BuilderCell[];
    rep: CompanionRep;
    /** +X offset from the shared origin, so several do not stack. */
    offsetX: number;
    /** Derived key of the last compiled result, when there is one. */
    latestGlbKey: string | null;
}

interface CompanionState {
    /** Keyed by model id. */
    companions: Record<string, CompanionModel>;
    add: (m: CompanionModel) => void;
    remove: (modelId: string) => void;
    setRep: (modelId: string, rep: CompanionRep) => void;
    setOffsetX: (modelId: string, offsetX: number) => void;
    /** Drop every companion — used when the scene is cleared wholesale. */
    clear: () => void;
}

export const useCompanionModelStore = create<CompanionState>()((set) => ({
    companions: {},
    add: (m) => set((s) => ({companions: {...s.companions, [m.modelId]: m}})),
    remove: (modelId) =>
        set((s) => {
            if (!(modelId in s.companions)) return s;
            const next = {...s.companions};
            delete next[modelId];
            return {companions: next};
        }),
    setRep: (modelId, rep) =>
        set((s) => {
            const cur = s.companions[modelId];
            if (!cur || cur.rep === rep) return s;
            return {companions: {...s.companions, [modelId]: {...cur, rep}}};
        }),
    setOffsetX: (modelId, offsetX) =>
        set((s) => {
            const cur = s.companions[modelId];
            if (!cur || cur.offsetX === offsetX) return s;
            return {companions: {...s.companions, [modelId]: {...cur, offsetX}}};
        }),
    clear: () => set({companions: {}}),
}));

/** The scene-source name a companion's compiled result loads under.
 *
 * Derived from the model, never from whatever happens to be active — that is
 * the mistake the side-by-side offset made, and it left a row unable to tell
 * whether its own result was in the scene. */
export function companionSourceName(name: string, rep: CompanionRep): string {
    return rep === "detail" ? `procedural-detail:${name}` : `procedural:${name}`;
}

/** Companions currently drawing topology, which is the renderer's input. */
export function topologyCompanions(state: CompanionState): CompanionModel[] {
    return Object.values(state.companions).filter((c) => c.rep === "topology");
}
