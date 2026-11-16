/**
 * Which builder components the RESIZE (S) gizmo works on, and the face-resize of a
 * rotated equipment box. Pure (three's maths only), so node-testable.
 *
 * A space, an opening and an equipment unit are boxes whose size the document stores
 * (DX/DY/DZ, LX/LY/LZ), so all three resize. Two cannot:
 *  - a loft band is the sweep between its member's section rings: its size is the
 *    station's section, typed with S on the band, not a box to drag;
 *  - an equipment whose box is fitted to its loaded CAD model ("Use CAD models" on and a
 *    CAD asset linked to its type): the box drawn is the CAD's bounds, which a new
 *    LX/LY/LZ does not change.
 */

import * as THREE from "three";
import {applyFaceOffset, type CellBox, type Vec3} from "./snap";

export type BuilderCellKind = "cell" | "equipment" | "opening" | "loft";

export const LOFT_RESIZE_REFUSAL =
    "A loft band is sized by its member's section rings: press S on the band to type the station's section";
export const CAD_RESIZE_REFUSAL =
    "This equipment's box follows its CAD model while Use CAD models is on: turn that off to resize it";

/** Why the resize gizmo refuses a component of `kind`, or null when it resizes. */
export function resizeRefusal(kind: BuilderCellKind, opts: {cadFitted?: boolean} = {}): string | null {
    if (kind === "loft") return LOFT_RESIZE_REFUSAL;
    if (kind === "equipment" && opts.cadFitted) return CAD_RESIZE_REFUSAL;
    return null;
}

/**
 * Whether an equipment's box is fitted to its CAD model: "Use CAD models" is on and its
 * type (by slug, then name) has a CAD asset linked. Store-only, so the menus can ask it;
 * the scene also treats a box it actually fitted to a loaded CAD mesh as fitted.
 */
export function equipmentCadFitted(
    cell: {kind: BuilderCellKind; equipmentType?: string},
    st: {equipmentCad: boolean; equipmentTypes: readonly {slug: string; name: string; has_cad?: boolean}[]},
): boolean {
    if (cell.kind !== "equipment" || !st.equipmentCad || !cell.equipmentType) return false;
    const key = cell.equipmentType.toLowerCase();
    const t =
        st.equipmentTypes.find((o) => o.slug.toLowerCase() === key) ??
        st.equipmentTypes.find((o) => o.name.toLowerCase() === key);
    return Boolean(t?.has_cad);
}

/** Whether a rotation (degrees, X/Y/Z) turns the box at all. */
export function isRotated(rotation: Vec3 | undefined | null): rotation is Vec3 {
    return !!rotation && (rotation[0] !== 0 || rotation[1] !== 0 || rotation[2] !== 0);
}

/** The box's pivot: its footprint centre (X/Y centre, bottom Z), which equipment spins about. */
export function footprintPivot(box: CellBox): Vec3 {
    return [box.origin[0] + box.size[0] / 2, box.origin[1] + box.size[1] / 2, box.origin[2]];
}

/** The rotation as a three Euler, in the order the store and the compiler use (ZYX). */
export function rotationEuler(rotation: Vec3): THREE.Euler {
    const r = THREE.MathUtils.degToRad;
    return new THREE.Euler(r(rotation[0]), r(rotation[1]), r(rotation[2]), "ZYX");
}

/**
 * The box's own frame: maps a point given in the box's unrotated (stored origin/size)
 * frame to where the rotated box puts it, spun about its footprint centre. Identity for
 * an unrotated box.
 */
export function boxFrameMatrix(box: CellBox, rotation: Vec3 | undefined | null): THREE.Matrix4 {
    if (!isRotated(rotation)) return new THREE.Matrix4();
    const p = footprintPivot(box);
    return new THREE.Matrix4()
        .makeTranslation(p[0], p[1], p[2])
        .multiply(new THREE.Matrix4().makeRotationFromEuler(rotationEuler(rotation)))
        .multiply(new THREE.Matrix4().makeTranslation(-p[0], -p[1], -p[2]));
}

/**
 * Move one face of a (possibly rotated) box by `offset` along its own axis, the opposite
 * face staying where it is on screen. Unrotated, this is `applyFaceOffset`; rotated, the
 * stored origin shifts so the box's pivot follows the resized footprint.
 */
export function resizeBoxFace(
    box: CellBox,
    rotation: Vec3 | undefined | null,
    axis: 0 | 1 | 2,
    positiveFace: boolean,
    offset: number,
    minSize = 0.1,
): CellBox {
    const local = applyFaceOffset(box, axis, positiveFace, offset, minSize);
    if (!isRotated(rotation)) return local;
    const pivot = new THREE.Vector3(...footprintPivot(local)).applyMatrix4(boxFrameMatrix(box, rotation));
    const round = (v: number) => Math.round(v * 1e9) / 1e9;
    return {
        origin: [round(pivot.x - local.size[0] / 2), round(pivot.y - local.size[1] / 2), round(pivot.z)],
        size: local.size,
    };
}
