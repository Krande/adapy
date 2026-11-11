// No three.js in this module or what it imports: both viewports read the planes from here.
import {create} from "zustand";

import type {ViewerActionKind} from "./viewerHistoryStore";
import {activeViewport} from "@/viewport/api/viewportApi";
import {recordViewerAction} from "@/utils/scene/viewerHistory";

// A single section / clipping plane. The three.js plane equation is
// normal·x + constant = 0; geometry on the +normal side is kept.
export interface SectionPlane {
    id: string;
    label: string;
    normal: [number, number, number];  // unit
    constant: number;
    enabled: boolean;
}

const AXIS_NORMAL: Record<"x" | "y" | "z", [number, number, number]> = {
    x: [1, 0, 0],
    y: [0, 1, 0],
    z: [0, 0, 1],
};

// Adding, moving, flipping, switching and removing planes are undo / redo steps (Ctrl+Z /
// Ctrl+Y); a gizmo drag calls `setConstant` on every move, and those moves coalesce into one step.
// The active plane, the gizmo's visibility and the cap colour are not steps.
export const sectionPlaneSlices: {get: () => unknown; set: (value: unknown) => void}[] = [
    {get: () => useSectionStore.getState().planes, set: (v) => useSectionStore.setState({planes: v as SectionPlane[]})},
    {get: () => useSectionStore.getState().activeId, set: (v) => useSectionStore.setState({activeId: v as string | null})},
];

/** Runs a change to the planes as one undo step. */
const recorded = (kind: ViewerActionKind, fn: () => void, coalesce?: string): void =>
    recordViewerAction(kind, fn, {slices: sectionPlaneSlices, coalesce});

let _seq = 0;
const nextId = () => `sp_${++_seq}`;

interface SectionState {
    planes: SectionPlane[];
    activeId: string | null;        // plane the drag gizmo is attached to
    gizmoVisible: boolean;          // show/hide the drag gizmo (navigate safely)
    capColor: string;               // fill colour of the cut cross-section
    addPlane: (axis: "x" | "y" | "z") => void;
    removePlane: (id: string) => void;
    toggle: (id: string) => void;
    setConstant: (id: string, constant: number) => void;
    flip: (id: string) => void;
    setActive: (id: string | null) => void;
    setGizmoVisible: (v: boolean) => void;
    setCapColor: (c: string) => void;
    clearAll: () => void;
}

export const useSectionStore = create<SectionState>((set) => ({
    planes: [],
    activeId: null,
    gizmoVisible: true,
    capColor: "#9099a1",  // ~ the default geometry grey
    addPlane: (axis) => recorded("plane-add", () =>
        set((s) => {
            const normal = AXIS_NORMAL[axis];
            // Pass the plane through the model centre by default.
            const b = activeViewport()?.modelBounds() ?? null;
            const c = b ? [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2] : [0, 0, 0];
            const constant = -(normal[0] * c[0] + normal[1] * c[1] + normal[2] * c[2]);
            const id = nextId();
            const count = s.planes.filter((p) => p.label.startsWith(axis.toUpperCase())).length;
            const label = count ? `${axis.toUpperCase()}-${count + 1}` : axis.toUpperCase();
            return {
                planes: [...s.planes, {id, label, normal, constant, enabled: true}],
                activeId: id,
            };
        })),
    removePlane: (id) => recorded("plane-remove", () =>
        set((s) => ({
            planes: s.planes.filter((p) => p.id !== id),
            activeId: s.activeId === id ? null : s.activeId,
        }))),
    toggle: (id) => recorded("plane-edit", () =>
        set((s) => ({planes: s.planes.map((p) => (p.id === id ? {...p, enabled: !p.enabled} : p))}))),
    setConstant: (id, constant) => recorded("plane-edit", () =>
        set((s) => ({planes: s.planes.map((p) => (p.id === id ? {...p, constant} : p))})), `plane-move:${id}`),
    flip: (id) => recorded("plane-edit", () =>
        set((s) => ({
            planes: s.planes.map((p) =>
                p.id === id
                    ? {...p, normal: [-p.normal[0], -p.normal[1], -p.normal[2]], constant: -p.constant}
                    : p,
            ),
        }))),
    setActive: (id) => set({activeId: id}),
    setGizmoVisible: (gizmoVisible) => set({gizmoVisible}),
    setCapColor: (capColor) => set({capColor}),
    clearAll: () => recorded("plane-clear", () => set({planes: [], activeId: null})),
}));
