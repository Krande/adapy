import {sceneRef, cameraRef, controlsRef} from "@/state/refs";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useModelState} from "@/state/modelState";
import {useTreeViewStore} from "@/state/treeViewStore";
import {requestRender} from "@/state/perfStore";
import {centerViewOnSelection} from "@/utils/scene/centerViewOnSelection";
import {unhideAllRanges} from "@/utils/scene/visibility";
import {queryNameFromRangeId} from "@/utils/mesh_select/queryMeshDrawRange";
import {query_ws_server_mesh_info} from "@/utils/mesh_select/handlers/send_mesh_selected_info_callback";
import {computeRangeStats} from "@/utils/mesh_select/meshStats";
import type {GeomWalkOrder} from "@/state/galleryStore";

// Geom-level gallery walk: enumerate every draw-range (geom) currently
// in the scene, then select + frame them one at a time. The walk order
// is "scene" (traversal order), "density" (triangles per surface area,
// heaviest first), or "tree" (the model tree's hierarchy order).

export interface GeomEntry {
    mesh: CustomBatchedMesh;
    rangeId: string;
    triangles: number;
    density: number;
}

export type WalkOrder = GeomWalkOrder | "tree";

function allBatchedMeshes(): CustomBatchedMesh[] {
    const out: CustomBatchedMesh[] = [];
    sceneRef.current?.traverse((o) => {
        if (o instanceof CustomBatchedMesh) out.push(o);
    });
    return out;
}

function sceneOrderEntries(meshes: CustomBatchedMesh[], withDensity: boolean): GeomEntry[] {
    const entries: GeomEntry[] = [];
    for (const mesh of meshes) {
        for (const rangeId of mesh.drawRanges.keys()) {
            if (withDensity) {
                const s = computeRangeStats(mesh, rangeId);
                entries.push({mesh, rangeId, triangles: s?.triangles ?? 0, density: s?.density ?? 0});
            } else {
                const range = mesh.drawRanges.get(rangeId)!;
                entries.push({mesh, rangeId, triangles: Math.floor(range[1] / 3), density: 0});
            }
        }
    }
    return entries;
}

function treeOrderEntries(meshes: CustomBatchedMesh[]): GeomEntry[] {
    const byKey = new Map<string, CustomBatchedMesh>();
    for (const m of meshes) byKey.set(m.unique_key, m);

    const treeData = useTreeViewStore.getState().treeData;
    if (!treeData) return sceneOrderEntries(meshes, false);

    const entries: GeomEntry[] = [];
    const seen = new Set<string>();
    const visit = (node: any) => {
        const rid = node?.rangeId;
        const mk = node?.model_key;
        if (rid != null && mk != null) {
            const mesh = byKey.get(String(mk));
            const rangeId = String(rid);
            const dedupKey = `${mk}|${rangeId}`;
            if (mesh && mesh.drawRanges.has(rangeId) && !seen.has(dedupKey)) {
                seen.add(dedupKey);
                const range = mesh.drawRanges.get(rangeId)!;
                entries.push({mesh, rangeId, triangles: Math.floor(range[1] / 3), density: 0});
            }
        }
        if (Array.isArray(node?.children)) for (const c of node.children) visit(c);
    };
    visit(treeData);

    // Tree may not cover every batched geom (e.g. FEA overlays with no
    // tree rows) — fall back to scene order when it yields nothing.
    return entries.length > 0 ? entries : sceneOrderEntries(meshes, false);
}

export function collectGeomEntries(order: WalkOrder): GeomEntry[] {
    const meshes = allBatchedMeshes();
    if (order === "tree") return treeOrderEntries(meshes);
    const entries = sceneOrderEntries(meshes, order === "density");
    if (order === "density") entries.sort((x, y) => y.density - x.density);
    return entries;
}

// Hide every draw-range except the kept (mesh, rangeId). hideBatchDrawRange
// is additive-only, so reset with unhideAllRanges() first.
function hideAllExcept(keepMesh: CustomBatchedMesh, keepRangeId: string): void {
    unhideAllRanges();
    for (const mesh of allBatchedMeshes()) {
        const toHide: string[] = [];
        for (const id of mesh.drawRanges.keys()) {
            if (mesh === keepMesh && id === keepRangeId) continue;
            toHide.push(id);
        }
        if (toHide.length) mesh.hideBatchDrawRange(toHide);
    }
    requestRender();
}

// Select the entry, populate the Object Info panel, optionally isolate
// it, and frame the camera on it (fit object).
export async function focusGeomEntry(entry: GeomEntry, opts: {hideUnselected: boolean}): Promise<void> {
    const {mesh, rangeId} = entry;

    const sel = useSelectedObjectStore.getState();
    sel.clearSelectedObjects();

    if (opts.hideUnselected) hideAllExcept(mesh, rangeId);
    else unhideAllRanges();

    sel.addSelectedObject(mesh, rangeId);

    // Info panel: same fields the click handler populates.
    const name = await queryNameFromRangeId(mesh.unique_key, rangeId);
    const info = useObjectInfoStore.getState();
    info.setName(name);
    const activeFile = useModelState.getState().loadedSourceName;
    info.setFileName(activeFile ?? null);
    info.setJsonData(null);
    info.setFaceIndex(null);
    if (name) {
        try {
            void query_ws_server_mesh_info(name, 0, activeFile);
        } catch {
            /* metadata is best-effort; selection + framing still work */
        }
    }

    const controls = controlsRef.current;
    const camera = cameraRef.current;
    if (controls && camera) centerViewOnSelection(controls, camera, 1.5);
    requestRender();
}

// Leave a geom walk cleanly: drop the isolation and the selection so the
// scene returns to its normal state (used when the walk type changes,
// gallery mode is turned off, or the scope is reloaded).
export function endGeomWalk(): void {
    unhideAllRanges();
    useSelectedObjectStore.getState().clearSelectedObjects();
    requestRender();
}
