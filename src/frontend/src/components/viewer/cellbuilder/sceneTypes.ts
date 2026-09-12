/**
 * Cellbuilder SCENE TYPES.
 *
 * Owns: the shapes of the scene controller's in-flight interaction state —
 * an active face drag, what is hovered, the axis-locked modal move, and the
 * four keyboard entry state machines. They live here (not inside one module)
 * because the scene context carries them and several modules read them.
 */

import * as THREE from "three";

import type {CellBox, EdgeHit, Vec3} from "@/utils/cellbuilder/snap";

/** An in-progress face drag (positive face scales size, negative shifts origin). */
export interface DragState {
    cellId: string;
    faceIndex: number;
    axis: 0 | 1 | 2;
    positiveFace: boolean;
    startBox: CellBox;
    // line through the face center along the face axis, world coords
    lineOrigin: THREE.Vector3;
    lineDir: THREE.Vector3;
    startT: number;
    startClientX: number;
    startClientY: number;
    started: boolean;
    pointerId: number;
}

/** The picked box face under the cursor. */
export interface HoveredFace {
    mesh: THREE.Mesh;
    faceIndex: number;
}

/** The picked face-border edge under the cursor. */
export interface HoveredEdge {
    cellId: string;
    faceIndex: number;
    edge: EdgeHit;
}

/** Blender-style axis-locked move: the cell tracks the pointer along one axis
 * with no click-drag; left-click confirms, Escape restores `startBox`. */
export interface ModalMove {
    cellId: string;
    axis: 0 | 1 | 2;
    lineOrigin: THREE.Vector3; // world-space point on the constraint axis
    lineDir: THREE.Vector3; // unit axis direction
    startT: number | null; // ray param at grab start (null until first move)
    startBox: CellBox; // pre-move box, for cancel
}

/** A tap on a cell that resolves to a selection on pointerup (face-drag off). */
export interface PendingSelect {
    cellId: string;
    faceIndex: number;
    x: number;
    y: number;
}

/** A tap on empty space that exits the active gizmo on pointerup. */
export interface PendingGizmoExit {
    x: number;
    y: number;
}

/** The loft station keyboard station edits (S/T) act on. */
export interface LoftActive {
    member: string;
    index: number;
}

/** Live keyboard numeric entry: a box extrude, a loft stack extension or a
 * loft station resize. The store is mutated only when one commits. */
export type NumEntry =
        | {
              kind: "cellExtrude";
              cellId: string;
              faceIndex: number;
              axis: 0 | 1 | 2;
              defaultDepth: number;
              typed: string;
          }
        | {
              kind: "loftExtend";
              memberName: string;
              defaultSpacing: number;
              anchor: Vec3;
              typed: string;
          }
        | {
              kind: "loftResize";
              memberName: string;
              stationIndex: number;
              section: "rectangle" | "circle";
              defaultVal: number;
              anchor: Vec3;
              typed: string;
          };

/** Numeric add-mode placement: type X, Y, Z and drop the box there. */
export interface PlaceEntry {
    axis: 0 | 1 | 2;
    vals: [number | null, number | null, number | null];
    typed: string;
}

/** Keyboard equipment insert: pick a type + host cell, then type cell-local X/Y. */
export interface EquipEntry {
    phase: "pick" | "xy";
    hostId: string;
    axis: 0 | 1; // xy phase: 0=X, 1=Y (cell-local)
    vals: [number | null, number | null];
    typed: string;
}

/** Keyboard opening-on-face insert: X/Y, W/H then DEPTH in the face plane. */
export interface OpenEntry {
    cellId: string;
    faceIndex: number;
    field: 0 | 1 | 2 | 3 | 4;
    vals: [number, number, number, number, number]; // X, Y, W, H, DEPTH
    typed: string;
}
