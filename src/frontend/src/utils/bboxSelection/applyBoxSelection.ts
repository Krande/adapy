/**
 * Selection boxes against the scene: a box made around the current selection, the
 * selection a box makes, and the selection a rectangle drawn on the screen makes.
 */

import * as THREE from "three";

import {useBBoxSelectionStore, type SelectionBox} from "@/state/bboxSelectionStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {recordViewerAction} from "@/utils/scene/viewerHistory";
import {sectionClippingPlanes} from "@/utils/scene/section_clipping";

import {padded, type Aabb} from "./boxMath";
import {boundsOfRanges, rangesInBox} from "./rangeBounds";
import {rangesInScreenRect, type NdcRect, type ScreenBoxMode} from "./screenBox";

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

/** The world AABB of the current selection, or null when nothing selected has geometry. */
export function selectionBounds(): Aabb | null {
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

/** A new selection box around the current selection, a little larger so what it was made
 *  from is fully inside. Returns its id, or null when nothing selected has geometry. */
export function addBoxAroundSelection(): string | null {
    const b = selectionBounds();
    if (!b) return null;
    const diag = Math.hypot(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]);
    const id = useBBoxSelectionStore.getState().add(padded(b, Math.max(0.05, diag * 0.02)));
    applyBoxSelection(id);
    return id;
}

/** Replace the selection with what box `id` takes. Returns how many elements that is. */
export function applyBoxSelection(id: string): number {
    const box = useBBoxSelectionStore.getState().boxes.find((b) => b.id === id);
    const scene = getViewerRuntime().scene.current;
    if (!box || !scene) return 0;
    const batch = boxBatch(box, scene);
    // Dragging a box's handles re-applies it on every move: the moves of one drag coalesce into
    // one undo entry.
    recordViewerAction("select", () => {
        const sel = useSelectedObjectStore.getState();
        sel.clearSelectedObjects();
        if (batch.length) sel.addBatchofMeshes(batch);
        const info = useObjectInfoStore.getState();
        info.setName(`${box.label}: ${batch.length} element${batch.length === 1 ? "" : "s"}`);
        info.setSelectedNodeId(null);
    }, {coalesce: `box:${id}`});
    requestRender();
    return batch.length;
}

/** Add what a rectangle drawn on the screen takes (`screenBox`) to the selection. Returns how
 *  many elements it took. */
export function selectInScreenRect(rect: NdcRect, mode: ScreenBoxMode, camera: THREE.Camera): number {
    const scene = getViewerRuntime().scene.current;
    if (!scene) return 0;
    camera.updateMatrixWorld();
    const viewProj = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    const batch: [CustomBatchedMesh, string][] = [];
    // Only what is drawn is taken: hidden ranges, hidden models and ranges a section plane cuts
    // away whole are skipped where the candidates are collected, so the count and the highlight
    // agree with the screen.
    const planes = sectionClippingPlanes();
    for (const mesh of visibleMeshes(scene)) {
        for (const id of rangesInScreenRect(mesh, viewProj, rect, mode, mesh.getHiddenRanges(), planes)) batch.push([mesh, id]);
    }
    if (batch.length) {
        // The whole drag is one undo entry (Ctrl+Z), however many elements it took.
        recordViewerAction("select", () => {
            useSelectedObjectStore.getState().addBatchofMeshes(batch);
            const info = useObjectInfoStore.getState();
            info.setName(`${mode === "window" ? "Window" : "Crossing"} selection: ${batch.length} element${batch.length === 1 ? "" : "s"}`);
            info.setSelectedNodeId(null);
        });
    }
    requestRender();
    return batch.length;
}

function boxBatch(box: SelectionBox, scene: THREE.Object3D): [CustomBatchedMesh, string][] {
    const bounds: Aabb = {min: box.min, max: box.max};
    const batch: [CustomBatchedMesh, string][] = [];
    const planes = sectionClippingPlanes();
    for (const mesh of visibleMeshes(scene)) {
        for (const id of rangesInBox(mesh, bounds, box.mode, mesh.getHiddenRanges(), planes)) batch.push([mesh, id]);
    }
    return batch;
}
