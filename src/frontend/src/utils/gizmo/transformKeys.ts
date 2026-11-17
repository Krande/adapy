/**
 * The transform gizmo's KEY RULES and snap steps.
 *
 * Owns: which key brings up which gizmo (G / R / S), which key locks it to an
 * axis (X / Y / Z, the same key again releases), the steps a drag snaps to, and
 * when a key belongs to a form field instead. The cell builder's key handler and
 * a box a plugin places in the scene (plugins/sceneBox.ts) both read them here,
 * so the two answer the same keys the same way.
 *
 * No three.js in this module: the three.js-free viewport's side loads it.
 */

export type GizmoAxis = 0 | 1 | 2;
export type GizmoKeyMode = "translate" | "rotate" | "resize";

/** The rotate rings snap to this many degrees. */
export const GIZMO_ROTATE_SNAP_DEG = 15;
/** The step a move snaps to where no grid step is set. */
export const GIZMO_FALLBACK_STEP = 0.1;

/** The step a move snaps to for a grid step of `gridStep` (none, or zero: the fallback). */
export function gizmoMoveStep(gridStep: number | null | undefined): number {
    return gridStep && gridStep > 0 ? gridStep : GIZMO_FALLBACK_STEP;
}

/** The gizmo a (lower-cased) key brings up: G translate, R rotate, S resize; null for any other. */
export function gizmoModeOfKey(key: string): GizmoKeyMode | null {
    return key === "g" ? "translate" : key === "r" ? "rotate" : key === "s" ? "resize" : null;
}

/** The axis a (lower-cased) key locks the gizmo to: X, Y or Z; null for any other. */
export function gizmoAxisOfKey(key: string): GizmoAxis | null {
    return key === "x" ? 0 : key === "y" ? 1 : key === "z" ? 2 : null;
}

/** The lock after `axis`'s key was pressed: that axis, or none when it was the one held. */
export function toggledAxisLock(current: GizmoAxis | null, axis: GizmoAxis): GizmoAxis | null {
    return current === axis ? null : axis;
}

/** Whether a key event's target is a form field: its keys are the field's own, not a gizmo's. */
export function isTextEntry(target: unknown): boolean {
    const tag = (target as {tagName?: unknown} | null)?.tagName;
    return typeof tag === "string" && /^(INPUT|TEXTAREA|SELECT)$/.test(tag);
}
