/** Apply a group selection to the scene as VISIBILITY.
 *
 *  Selecting a group answers "which parts am I looking at", not "paint these differently".
 *  So an isolated set keeps its own appearance — the field colours, the deformation, the
 *  material it already had — and everything else stops being drawn, either entirely or
 *  down to a wireframe ghost. This deliberately does NOT touch the selection store: an
 *  earlier version highlighted the members blue, which recoloured the very thing you had
 *  isolated in order to look at it.
 *
 *  The three.js half of ``feaSets.ts``. Kept apart so the arithmetic there stays testable
 *  without a renderer, and so this file is the only place that knows how a group becomes
 *  pixels.
 */

import * as THREE from "three";

import {requestRender} from "@/state/perfStore";
import {sceneRef} from "@/state/refs";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

import {complementRanges} from "./feaSets";

/** The element-boundary wireframe overlays the FEA loader adds.
 *
 *  Matched by name rather than by type: these are plain LineSegments sharing the mesh's
 *  position buffer, and the scene holds other LineSegments (a GLB's own) that must not be
 *  touched. Absent when the "hide element edges" perf toggle was on at load, which is why
 *  every use tolerates finding none.
 */
const EDGE_OVERLAY_NAMES = ["fea-element-edges", "fea-beam-solid-element-edges"];

function edgeOverlays(): THREE.Object3D[] {
    const scene = sceneRef.current;
    if (!scene) return [];
    const found: THREE.Object3D[] = [];
    scene.traverse((o: THREE.Object3D) => {
        if (EDGE_OVERLAY_NAMES.includes(o.name)) found.push(o);
    });
    return found;
}

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

/** Show only ``memberIds``; hide the rest, as a wireframe ghost when ``wireframeRest``.
 *
 *  An empty selection shows everything — "no group chosen" is the whole model, not an
 *  empty viewport.
 *
 *  Each call describes the FULL desired state rather than a delta, which is why it
 *  unhides first: a shrinking selection has to reveal what it no longer covers, and the
 *  Outliner drives this on every selection change.
 *
 *  Node ids (``P{n}``) match no draw range, so a node-only selection would name nothing
 *  this mesh draws. Hiding everything then blanks the viewport and reads as a crash, so
 *  a selection that covers no range leaves the mesh alone — and the Outliner disables the
 *  wireframe toggle for node sets rather than letting them look broken.
 */
export function applyFeaGroupVisibility(memberIds: string[], wireframeRest: boolean): void {
    const meshes = batchedMeshes();
    for (const m of meshes) m.unhideAllDrawRanges();

    let isolating = false;
    if (memberIds.length > 0) {
        const keep = new Set(memberIds);
        for (const m of meshes) {
            const ghost = complementRanges(m.drawRanges.keys(), keep);
            if (ghost.length > 0 && ghost.length < m.drawRanges.size) {
                m.hideBatchDrawRange(ghost);
                isolating = true;
            }
        }
    }

    // What actually produces the wireframe ghost.
    //
    // Hiding a draw range hides its FACES; the element-boundary wireframe is a separate
    // LineSegments over the whole model, so hidden elements keep their outlines for free.
    // The ghost is the DEFAULT, and this toggle's real job is turning it off -- which is
    // the opposite of how it looks from the outside, and why an earlier attempt to build
    // the ghost by keeping per-range edges lit did nothing here at all.
    //
    // The overlay is one deduped edge buffer with no element association (a shared edge
    // belongs to two elements), so it cannot be filtered down to the visible set: turning
    // the ghost off takes the element boundaries off the isolated set too. Scoped to
    // isolation deliberately -- with nothing selected the outlines always come back, so
    // the ordinary view never silently loses them.
    for (const o of edgeOverlays()) o.visible = !isolating || wireframeRest;

    requestRender();
}

/** Restore every hidden range. */
export function clearFeaGroupVisibility(): void {
    for (const m of batchedMeshes()) m.unhideAllDrawRanges();
    for (const o of edgeOverlays()) o.visible = true;
    requestRender();
}
