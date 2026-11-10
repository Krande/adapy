/**
 * Box selection on the three.js scene: which draw ranges a selection box or a rectangle on the
 * screen takes, and the box around the selection. What the three.js viewport answers the viewport
 * interface with (viewport/api/boxSelection.ts); the triangles and the cached range bounds decide,
 * as in ./rangeBounds and ./screenBox.
 */

import * as THREE from "three";

import {useBBoxSelectionStore} from "@/state/bboxSelectionStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {sectionClippingPlanes} from "@/utils/scene/section_clipping";
import type {ObjectSet} from "@/viewport/api/boxSelection";
import type {ObjectRef} from "@/viewport/api/viewportApi";

import type {Aabb, BoxSelectMode} from "./boxMath";
import {boundsOfRanges, rangesInBox} from "./rangeBounds";
import {rangesInScreenRect} from "./screenBox";
import type {NdcRect, ScreenBoxMode} from "./screenRect";

function effectivelyVisible(o: THREE.Object3D): boolean {
    for (let cur: THREE.Object3D | null = o; cur; cur = cur.parent) if (!cur.visible) return false;
    return true;
}

function visibleMeshes(scene: THREE.Object3D): CustomBatchedMesh[] {
    const out: CustomBatchedMesh[] = [];
    scene.traverse((o) => {
        if (o instanceof CustomBatchedMesh && effectivelyVisible(o)) out.push(o);
    });
    return out;
}

/** Draw ranges, grouped by the mesh that draws them: the three.js viewport's `ObjectSet`. The
 *  viewport's `setSelection` takes the groups as they are; anything else reads them as object refs. */
export class RangeSet implements ObjectSet {
    readonly size: number;

    constructor(readonly groups: readonly (readonly [CustomBatchedMesh, readonly string[]])[]) {
        let n = 0;
        for (const [, ids] of groups) n += ids.length;
        this.size = n;
    }

    *[Symbol.iterator](): Generator<ObjectRef> {
        const ref: ObjectRef = {model: "", id: "", node: null};
        for (const [mesh, ids] of this.groups) {
            ref.model = mesh.unique_key ?? (mesh.userData?.["unique_hash"] as string | undefined) ?? "";
            ref.node = mesh.name || null;
            for (const id of ids) {
                ref.id = id;
                yield ref;
            }
        }
    }

    /** Each range with its mesh, for a caller that works on pairs. */
    pairs(): [CustomBatchedMesh, string][] {
        const out: [CustomBatchedMesh, string][] = [];
        for (const [mesh, ids] of this.groups) for (const id of ids) out.push([mesh, id]);
        return out;
    }
}

const NONE = new RangeSet([]);

/** The draw ranges of the scene the box takes in `mode`. Only what is drawn: hidden ranges, hidden
 *  models and ranges a section plane cuts away whole are skipped where the candidates are collected,
 *  so the count and the highlight agree with the screen. */
export function rangesInWorldBox(box: Aabb, mode: BoxSelectMode): RangeSet {
    const scene = getViewerRuntime().scene.current;
    if (!scene) return NONE;
    const groups: [CustomBatchedMesh, string[]][] = [];
    const planes = sectionClippingPlanes();
    for (const mesh of visibleMeshes(scene)) {
        const ids = rangesInBox(mesh, box, mode, mesh.getHiddenRanges(), planes);
        if (ids.length) groups.push([mesh, ids]);
    }
    return new RangeSet(groups);
}

/** The draw ranges of the scene a rectangle on the screen takes (`screenBox`), seen by `camera`. */
export function rangesInCameraRect(rect: NdcRect, mode: ScreenBoxMode, camera: THREE.Camera): RangeSet {
    const scene = getViewerRuntime().scene.current;
    if (!scene) return NONE;
    camera.updateMatrixWorld();
    const viewProj = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    const groups: [CustomBatchedMesh, string[]][] = [];
    const planes = sectionClippingPlanes();
    for (const mesh of visibleMeshes(scene)) {
        const ids = rangesInScreenRect(mesh, viewProj, rect, mode, mesh.getHiddenRanges(), planes);
        if (ids.length) groups.push([mesh, ids]);
    }
    return new RangeSet(groups);
}

/** The world AABB of the current selection, or null when nothing selected has geometry. */
export function threeSelectionBounds(): Aabb | null {
    let out: Aabb | null = null;
    for (const [obj, ids] of useSelectedObjectStore.getState().selectedObjects) {
        if (!(obj instanceof CustomBatchedMesh)) continue;
        const b = boundsOfRanges(obj, ids);
        if (!b) continue;
        if (!out) {
            out = b;
            continue;
        }
        for (let k = 0; k < 3; k++) {
            out.min[k] = Math.min(out.min[k], b.min[k]);
            out.max[k] = Math.max(out.max[k], b.max[k]);
        }
    }
    return out;
}

/** What box `id` takes in `mode` (its own mode when omitted), without touching the selection:
 *  the same elements "Reselect" would select. Empty when the box or the scene is gone. */
export function boxRanges(id: string, mode?: BoxSelectMode): [CustomBatchedMesh, string][] {
    const box = useBBoxSelectionStore.getState().boxes.find((b) => b.id === id);
    if (!box) return [];
    return rangesInWorldBox({min: box.min, max: box.max}, mode ?? box.mode).pairs();
}
