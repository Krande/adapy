import {create} from "zustand";
import * as THREE from "three";

import {useModelState} from "./modelState";

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
    addPlane: (axis) =>
        set((s) => {
            const normal = AXIS_NORMAL[axis];
            // Pass the plane through the model centre by default.
            const bb = useModelState.getState().boundingBox;
            const c = bb ? bb.getCenter(new THREE.Vector3()) : new THREE.Vector3();
            const constant = -(normal[0] * c.x + normal[1] * c.y + normal[2] * c.z);
            const id = nextId();
            const count = s.planes.filter((p) => p.label.startsWith(axis.toUpperCase())).length;
            const label = count ? `${axis.toUpperCase()}-${count + 1}` : axis.toUpperCase();
            return {
                planes: [...s.planes, {id, label, normal, constant, enabled: true}],
                activeId: id,
            };
        }),
    removePlane: (id) =>
        set((s) => ({
            planes: s.planes.filter((p) => p.id !== id),
            activeId: s.activeId === id ? null : s.activeId,
        })),
    toggle: (id) =>
        set((s) => ({planes: s.planes.map((p) => (p.id === id ? {...p, enabled: !p.enabled} : p))})),
    setConstant: (id, constant) =>
        set((s) => ({planes: s.planes.map((p) => (p.id === id ? {...p, constant} : p))})),
    flip: (id) =>
        set((s) => ({
            planes: s.planes.map((p) =>
                p.id === id
                    ? {...p, normal: [-p.normal[0], -p.normal[1], -p.normal[2]], constant: -p.constant}
                    : p,
            ),
        })),
    setActive: (id) => set({activeId: id}),
    setGizmoVisible: (gizmoVisible) => set({gizmoVisible}),
    setCapColor: (capColor) => set({capColor}),
    clearAll: () => set({planes: [], activeId: null}),
}));
