/** Apply a set selection to the scene: highlight the members, and optionally ghost
 *  everything else down to a wireframe.
 *
 *  The three.js half of ``feaSets.ts``. Kept apart so the arithmetic there stays testable
 *  without a renderer, and so this file can be the only place that knows how a set becomes
 *  pixels.
 */

import * as THREE from "three";

import {sceneRef} from "@/state/refs";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {setActiveFeaSelectedRangeIds} from "@/utils/scene/handlers/load_fea_streaming";
import {requestRender} from "@/state/perfStore";

import {complementRanges} from "./feaSets";

/** Every CustomBatchedMesh currently in the scene.
 *
 *  Found by traversal rather than asked of the FEA loader, matching what
 *  ``unhideAllRanges`` already does. A streaming result is one mesh today, but traversing
 *  costs nothing and keeps this correct if a result ever arrives split. */
function batchedMeshes(): CustomBatchedMesh[] {
    const scene = sceneRef.current;
    if (!scene) return [];
    const found: CustomBatchedMesh[] = [];
    scene.traverse((o: THREE.Object3D) => {
        if (o instanceof CustomBatchedMesh) found.push(o);
    });
    return found;
}

/** Highlight ``memberIds`` and, when ``wireframeRest`` is on, hide the faces of everything
 *  else while leaving its edges drawn.
 *
 *  Order matters. Unhiding first means a shrinking selection reveals what it no longer
 *  covers instead of accumulating ghosts across calls — the panel drives this on every
 *  checkbox click, so each call has to describe the whole desired state rather than a
 *  delta.
 *
 *  Node ids (``P{n}``) are passed through untouched: they match no draw range, so they
 *  neither highlight nor ghost anything. That is why the panel disables the wireframe
 *  toggle for a node-only selection rather than letting it hide the entire model.
 */
export function applyFeaSetSelection(memberIds: string[], wireframeRest: boolean): void {
    const meshes = batchedMeshes();
    for (const m of meshes) m.unhideAllDrawRanges();

    setActiveFeaSelectedRangeIds(memberIds);

    if (wireframeRest && memberIds.length > 0) {
        const keep = new Set(memberIds);
        for (const m of meshes) {
            const ghost = complementRanges(m.drawRanges.keys(), keep);
            // Everything ghosted means the selection named nothing this mesh draws — a
            // node-only set, or a set from another model. Hiding the lot would blank the
            // viewport and read as a crash, so leave the mesh alone.
            if (ghost.length > 0 && ghost.length < m.drawRanges.size) {
                m.hideBatchDrawRange(ghost, true);
            }
        }
    }
    requestRender();
}

/** Drop the highlight and restore every hidden range. */
export function clearFeaSetSelection(): void {
    for (const m of batchedMeshes()) m.unhideAllDrawRanges();
    setActiveFeaSelectedRangeIds([]);
    requestRender();
}
