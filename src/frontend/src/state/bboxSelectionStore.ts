import {create} from "zustand";

import type {Aabb, BoxSelectMode, Vec3} from "@/utils/bboxSelection/boxMath";
import {highestBoxNumber} from "@/utils/bboxSelection/boxStorage";
import {recordViewerAction, type StateSlice} from "@/utils/scene/viewerHistory";
import type {ViewerActionKind} from "@/state/viewerHistoryStore";

// Selection boxes: world-space boxes that select the elements they touch or contain.
// Plain data only; BBoxSelectionController owns the three.js side. `boxes` and `activeId` are
// kept per scope in this browser by utils/bboxSelection/boxPersistence.

export interface SelectionBox {
    id: string;
    label: string;
    min: Vec3;
    max: Vec3;
    mode: BoxSelectMode;
    /** Hex colour, e.g. "#33aaff". */
    color: string;
    /** 0..1, of the box's faces; its edges stay opaque. */
    opacity: number;
    visible: boolean;
}

export interface SelectionBoxMenu {
    boxId: string;
    x: number;
    y: number;
}

interface BBoxSelectionState {
    boxes: SelectionBox[];
    /** The box the gizmo and handles are on, and whose selection is applied. */
    activeId: string | null;
    menu: SelectionBoxMenu | null;
    add: (bounds: Aabb) => string;
    update: (id: string, patch: Partial<Omit<SelectionBox, "id">>) => void;
    remove: (id: string) => void;
    setActive: (id: string | null) => void;
    openMenu: (menu: SelectionBoxMenu) => void;
    closeMenu: () => void;
    /** Replace every box, e.g. with the ones kept from an earlier page. Applies no selection. */
    restore: (boxes: SelectionBox[], activeId: string | null) => void;
    /** Delete every box. */
    clearAll: () => void;
}

const DEFAULT_COLOR = "#33aaff";
const DEFAULT_OPACITY = 0.15;

let counter = 0;

// Adding, editing and deleting boxes are undo / redo steps (Ctrl+Z / Ctrl+Y): the boxes and the
// active one are the state an undo puts back, together with the selection the change made. A drag
// of a box's handles calls `update` on every move; those moves coalesce into one step. `restore`
// (a reload, a scope switch) and the menu are not steps.
const boxSlices: StateSlice[] = [
    {get: () => useBBoxSelectionStore.getState().boxes, set: (v) => useBBoxSelectionStore.setState({boxes: v as SelectionBox[]})},
    {get: () => useBBoxSelectionStore.getState().activeId, set: (v) => useBBoxSelectionStore.setState({activeId: v as string | null})},
];
/** Run `fn` as one undo step that may change the boxes (and the selection). */
export const recordBoxAction = <T>(kind: ViewerActionKind, fn: () => T, coalesce?: string): T =>
    recordViewerAction(kind, fn, {slices: boxSlices, coalesce});
const recorded = recordBoxAction;

export const useBBoxSelectionStore = create<BBoxSelectionState>()((set, get) => ({
    boxes: [],
    activeId: null,
    menu: null,
    add: (bounds) => recorded("box-add", () => {
        counter += 1;
        const id = `bbox-${Date.now().toString(36)}-${counter}`;
        const box: SelectionBox = {
            id,
            label: `Selection box ${counter}`,
            min: [...bounds.min],
            max: [...bounds.max],
            mode: "partial",
            color: DEFAULT_COLOR,
            opacity: DEFAULT_OPACITY,
            visible: true,
        };
        set({boxes: [...get().boxes, box], activeId: id});
        return id;
    }),
    update: (id, patch) =>
        recorded("box-edit", () => set({boxes: get().boxes.map((b) => (b.id === id ? {...b, ...patch} : b))}), `box-edit:${id}`),
    remove: (id) => recorded("box-remove", () => {
        const {boxes, activeId, menu} = get();
        set({
            boxes: boxes.filter((b) => b.id !== id),
            activeId: activeId === id ? null : activeId,
            menu: menu?.boxId === id ? null : menu,
        });
    }),
    setActive: (activeId) => set({activeId}),
    openMenu: (menu) => set({menu, activeId: menu.boxId}),
    closeMenu: () => set({menu: null}),
    restore: (boxes, activeId) => {
        counter = Math.max(counter, highestBoxNumber(boxes));
        set({boxes, activeId: boxes.some((b) => b.id === activeId) ? activeId : null, menu: null});
    },
    clearAll: () => recorded("box-clear", () => set({boxes: [], activeId: null, menu: null})),
}));
