/** Apply a set of element ids to the scene as VISIBILITY.
 *
 *  Isolating a group answers "which parts am I looking at", not "paint these differently".
 *  So an isolated group keeps its own appearance — the field colours, the deformation, the
 *  material it already had — and everything else stops being drawn, either entirely or
 *  down to a wireframe ghost. This deliberately does NOT touch the selection store: an
 *  earlier version highlighted the members blue, which recoloured the very thing you had
 *  isolated in order to look at it.
 *
 *  The three.js half of ``feaSets.ts``. Kept apart so the arithmetic there stays testable
 *  without a renderer, and so this file is the only place that knows how a set becomes
 *  pixels. In core rather than in a UI shell because more than one caller needs it: a
 *  shell's Outliner, and a result plugin scoping its view to the sets a check ran on.
 */

import * as THREE from "three";

import {requestRender} from "@/state/perfStore";
import {sceneRef} from "@/state/refs";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {syncFeaOverlayVisibility} from "@/utils/scene/handlers/load_fea_streaming";

import {visibleEdgeIndex} from "./feaEdgeFilter";
import {complementRanges} from "./feaSets";
import {setResultLineSegmentsIsolation} from "./resultLineSegments";

/** The element-boundary wireframe overlays the FEA loader adds.
 *
 *  Matched by name rather than by type: these are plain LineSegments sharing their mesh's
 *  position buffer, and the scene holds other LineSegments (a GLB's own) that must not be
 *  touched. Absent when the "hide element edges" perf toggle was on at load, which is why
 *  every use tolerates finding none.
 */
const EDGE_OVERLAY_NAMES = ["fea-element-edges", "fea-beam-element-edges"];

/** Where the untouched edge index is parked so filtering is reversible. */
const FULL_INDEX_KEY = "__adaFullEdgeIndex";

function edgeOverlays(): THREE.LineSegments[] {
    const scene = sceneRef.current;
    if (!scene) return [];
    const found: THREE.LineSegments[] = [];
    scene.traverse((o: THREE.Object3D) => {
        if (EDGE_OVERLAY_NAMES.includes(o.name)) found.push(o as THREE.LineSegments);
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

/** The original, unfiltered edge index — captured the first time it is needed, before any
 *  filtering has replaced it. */
function fullIndexOf(overlay: THREE.LineSegments): ArrayLike<number> | null {
    const cached = overlay.userData[FULL_INDEX_KEY] as ArrayLike<number> | undefined;
    if (cached) return cached;
    const idx = overlay.geometry.getIndex();
    if (!idx) return null;
    const arr = idx.array as ArrayLike<number>;
    overlay.userData[FULL_INDEX_KEY] = arr;
    return arr;
}

function restoreEdges(overlay: THREE.LineSegments): void {
    const full = overlay.userData[FULL_INDEX_KEY] as Uint32Array | undefined;
    if (!full) return;
    overlay.geometry.setIndex(new THREE.BufferAttribute(full, 1));
}

/** Show only ``memberIds`` (draw-range ids, ``E{n}``); hide the rest, as a wireframe ghost
 *  when ``wireframeRest``.
 *
 *  An empty selection shows everything — "no group chosen" is the whole model, not an
 *  empty viewport.
 *
 *  Each call describes the FULL desired state rather than a delta, which is why it
 *  unhides first: a shrinking selection has to reveal what it no longer covers, and a
 *  caller drives this on every selection change.
 *
 *  Node ids (``P{n}``) match no draw range, so a node-only selection would name nothing
 *  this mesh draws. Hiding everything then blanks the viewport and reads as a crash, so
 *  a selection that covers no range leaves the mesh alone.
 */
export function applyFeaGroupVisibility(memberIds: Iterable<string>, wireframeRest: boolean): void {
    const meshes = batchedMeshes();
    for (const m of meshes) m.unhideAllDrawRanges();

    const keep = memberIds instanceof Set ? (memberIds as Set<string>) : new Set(memberIds);
    // Does the selection name anything the scene actually draws? Asked across EVERY
    // mesh before hiding anything on any of them.
    //
    // Per-mesh was wrong in both directions. A beam-only selection -- a material used
    // by nothing but the mast legs -- names no range on the shell mesh, so that mesh
    // kept every plate while the beam-solid mesh isolated correctly, and the result
    // was a solid model with a slightly different mast. And a selection that names
    // nothing anywhere has to leave the model alone rather than blank the viewport,
    // which is what the old per-mesh size guard was really for.
    const matches =
        keep.size > 0 &&
        meshes.some((m) => {
            for (const id of m.drawRanges.keys()) if (keep.has(id)) return true;
            return false;
        });
    let isolating = false;
    if (matches) {
        for (const m of meshes) {
            const ghost = complementRanges(m.drawRanges.keys(), keep);
            if (ghost.length === 0) continue;
            m.hideBatchDrawRange(ghost);
            isolating = true;
        }
    }

    // The beams, which have no draw range to hide. Without this the one kind of
    // element a stiffener or a beam-section selection is made of was the one kind
    // the isolation could not touch: every plate vanished and every beam stayed.
    for (const m of meshes) {
        setResultLineSegmentsIsolation(m, isolating ? keep : null);
    }

    // Element boundaries.
    //
    // Hiding a draw range hides its FACES. The element-boundary wireframe is a separate
    // LineSegments over the whole model, so left alone it keeps drawing outlines for
    // elements that are no longer there — which is exactly the ghost we want when "show
    // rest as wireframe" is on, and exactly what we do not want when it is off.
    //
    // Off does not mean "no lines". The group you isolated has to keep its OWN element
    // boundaries, or it renders as a featureless solid and stops reading as a mesh at all.
    // So the overlay gets re-indexed down to the visible elements' edges rather than
    // switched off wholesale.
    for (const overlay of edgeOverlays()) {
        if (!isolating || wireframeRest) {
            restoreEdges(overlay);
            continue;
        }
        const mesh = overlay.parent;
        const full = fullIndexOf(overlay);
        if (!(mesh instanceof CustomBatchedMesh) || !full) continue;
        const filtered = visibleEdgeIndex(
            mesh.geometry.getIndex()?.array ?? null,
            mesh.drawRanges,
            keep,
            full,
        );
        if (filtered) overlay.geometry.setIndex(new THREE.BufferAttribute(filtered, 1));
    }

    // Whether a beam's grey edge is drawn at all belongs to the rule that also decides
    // between the section solid and the coloured line. Forcing it visible here
    // re-created the double-drawn beam every time you isolated something.
    syncFeaOverlayVisibility();
    requestRender();
}

/** Restore every hidden range and the full element-edge wireframe. */
export function clearFeaGroupVisibility(): void {
    for (const m of batchedMeshes()) {
        m.unhideAllDrawRanges();
        setResultLineSegmentsIsolation(m, null);
    }
    for (const o of edgeOverlays()) restoreEdges(o);
    syncFeaOverlayVisibility();
    requestRender();
}
