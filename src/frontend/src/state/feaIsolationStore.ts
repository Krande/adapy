import {create} from "zustand";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import type {FeaSetInfo, MembershipStatus, PropertyMembership} from "@/utils/scene/fea/feaTreeRows";

// The loaded FEA result's sets, which elements carry which property value, and
// what is isolated -- for the Scene tree's FEA rows, core's FEA isolation
// commands and the Selected Object panel's FEA section.
//
// The sets are the manifest's (inline, or its sidecar), put here when a result
// loads (`utils/scene/fea/streaming/manifestStores`). The memberships are read on
// demand, one property field at a time (`utils/scene/fea/feaPropertyMembership`).
// The isolation is one immutable object, so the undo history can hold it as a
// slice (`utils/scene/fea/feaIsolation`).
//
// In a pop-out window, which has no scene, `mirrored` holds what the main window
// publishes (`core:fea-tree`, `utils/scene/fea/feaTree`) and the rest stays empty.

/** What is isolated: the row ids (element sets, property values) and whether the rest is
 *  drawn as a wireframe. Replaced whole on every change. */
export interface FeaIsolation {
    readonly rows: readonly string[];
    readonly contextWireframe: boolean;
}

/** One property value of a picked element, for the Selected Object panel. */
export interface FeaPickedProperty {
    field: string;
    label: string;
    value: number;
    valueLabel: string;
    rowId: string;
}

/** What the FEA rows say about one picked element. */
export interface FeaPickedInfo {
    /** Its draw-range id, `E<label>`. */
    element: string;
    properties: FeaPickedProperty[];
    /** Property fields whose membership is still being read. */
    pending: number;
    /** The element sets naming it, as `{name, rowId}`. */
    sets: {name: string; rowId: string}[];
}

/** The state a pop-out window needs to draw the rows, the menus and the commands. */
export interface FeaTreeView {
    /** An FEA result is loaded, with sets or properties to show. */
    loaded: boolean;
    roots: readonly TreeNodeData[];
    isolation: FeaIsolation;
    /** Draw ranges the isolation keeps. */
    members: number;
    contextReason: string | null;
    isolationReason: string | null;
    picked: FeaPickedInfo | null;
}

interface FeaIsolationState {
    /** The source the sets belong to (`useFeaAnimationStore.sourceName`). */
    source: string | null;
    sets: readonly FeaSetInfo[];
    membership: ReadonlyMap<string, PropertyMembership>;
    membershipStatus: ReadonlyMap<string, MembershipStatus>;
    isolation: FeaIsolation;
    /** A pop-out window's copy of the main window's view; null in the main window. */
    mirrored: FeaTreeView | null;
    setSets: (source: string | null, sets: readonly FeaSetInfo[]) => void;
    /** Forget the sets, the memberships and the isolation: the result changed or went. */
    reset: (source: string | null) => void;
}

/** Nothing isolated; the rest drawn as a wireframe once something is (where you are in the model). */
export const NO_FEA_ISOLATION: FeaIsolation = Object.freeze({rows: Object.freeze([]) as readonly string[], contextWireframe: true});

export const useFeaIsolationStore = create<FeaIsolationState>()((set, get) => ({
    source: null,
    sets: [],
    membership: new Map(),
    membershipStatus: new Map(),
    isolation: NO_FEA_ISOLATION,
    mirrored: null,
    setSets: (source, sets) => {
        if (get().source !== source) get().reset(source);
        set({source, sets});
    },
    reset: (source) =>
        set((s) => ({
            source,
            sets: [],
            membership: new Map(),
            membershipStatus: new Map(),
            // The choice of drawing the rest stays: it is the user's, not the model's.
            isolation: {rows: [], contextWireframe: s.isolation.contextWireframe},
        })),
}));
