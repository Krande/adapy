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
import {computeRangeStats, DEFAULT_SPIKE_THRESHOLDS} from "@/utils/mesh_select/meshStats";
import type {SpikeThresholds} from "@/utils/mesh_select/meshStats";
import {useOptionsStore} from "@/state/optionsStore";
import type {GeomWalkOrder} from "@/state/galleryStore";

// Geom-level gallery walk: enumerate every draw-range (geom) currently
// in the scene, then select + frame them one at a time. The walk order
// is "scene" (traversal order), "density" (triangles per surface area,
// heaviest first), "tree" (the model tree's hierarchy order), or
// "distorted" (only geoms with a crows-nest spike, worst-first).

export interface GeomEntry {
    mesh: CustomBatchedMesh;
    rangeId: string;
    triangles: number;
    density: number;
    spike: number; // worst thin-triangle reach (fraction of bbox diagonal); see meshStats maxSpike
    spikeTris: number; // how many spike triangles this geom has
}

export type WalkOrder = GeomWalkOrder;

function allBatchedMeshes(): CustomBatchedMesh[] {
    const out: CustomBatchedMesh[] = [];
    sceneRef.current?.traverse((o) => {
        if (o instanceof CustomBatchedMesh) out.push(o);
    });
    return out;
}

function sceneOrderEntries(
    meshes: CustomBatchedMesh[],
    withStats: boolean,
    thresholds: SpikeThresholds = DEFAULT_SPIKE_THRESHOLDS,
): GeomEntry[] {
    const entries: GeomEntry[] = [];
    for (const mesh of meshes) {
        for (const rangeId of mesh.drawRanges.keys()) {
            if (withStats) {
                const s = computeRangeStats(mesh, rangeId, thresholds);
                entries.push({
                    mesh,
                    rangeId,
                    triangles: s?.triangles ?? 0,
                    density: s?.density ?? 0,
                    spike: s?.maxSpike ?? 0,
                    spikeTris: s?.spikeTris ?? 0,
                });
            } else {
                const range = mesh.drawRanges.get(rangeId)!;
                entries.push({mesh, rangeId, triangles: Math.floor(range[1] / 3), density: 0, spike: 0, spikeTris: 0});
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
                entries.push({mesh, rangeId, triangles: Math.floor(range[1] / 3), density: 0, spike: 0, spikeTris: 0});
            }
        }
        if (Array.isArray(node?.children)) for (const c of node.children) visit(c);
    };
    visit(treeData);

    // Tree may not cover every batched geom (e.g. FEA overlays with no
    // tree rows) — fall back to scene order when it yields nothing.
    return entries.length > 0 ? entries : sceneOrderEntries(meshes, false);
}

export function collectGeomEntries(
    order: WalkOrder,
    thresholds: SpikeThresholds = DEFAULT_SPIKE_THRESHOLDS,
): GeomEntry[] {
    const meshes = allBatchedMeshes();
    if (order === "tree") return treeOrderEntries(meshes);
    const entries = sceneOrderEntries(meshes, order === "density" || order === "distorted", thresholds);
    if (order === "density") entries.sort((x, y) => y.density - x.density);
    if (order === "distorted") {
        // Only geoms with an outlier-vertex spike, worst-first — the walk visits problems, not the
        // whole model. Empty result ⇒ nothing distorted (the good outcome). The Mesh panel passes
        // adjusted thresholds here to re-scan tighter/looser; the gallery uses the default.
        return entries.filter((e) => e.spike > thresholds.spikeOutlierK).sort((x, y) => y.spike - x.spike);
    }
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
// True only while the mesh is still attached to the live scene. A scope switch disposes the old
// scope's batched meshes; a walk holding stale entries would otherwise select/frame a freed mesh
// (disposed geometry -> crash). Guards every operation that touches a walked mesh.
function meshIsLive(mesh: CustomBatchedMesh | undefined | null): boolean {
    if (!mesh) return false;
    const scene = sceneRef.current;
    if (!scene) return false;
    let node: any = mesh;
    while (node) {
        if (node === scene) return true;
        node = node.parent;
    }
    return false;
}

export async function focusGeomEntry(
    entry: GeomEntry | undefined,
    opts: {hideUnselected: boolean; forceEdges?: boolean},
): Promise<void> {
    // A scope/scene transition can leave a stale or disposed entry in the walk — never crash on it.
    if (!entry || !meshIsLive(entry.mesh) || !entry.mesh.drawRanges.has(entry.rangeId)) return;
    const {mesh, rangeId} = entry;

    // Inspecting spikes is pointless without triangle edges — turn them on for the distorted walk.
    // (Geometry Edges default on; this re-asserts it. A user who loaded with edges off may need a
    // reload for the overlay to attach — the toggle itself is reload-gated.)
    if (opts.forceEdges && !useOptionsStore.getState().showEdges) {
        useOptionsStore.getState().setShowEdges(true);
    }

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

    // The name lookup above awaited; a scope switch may have disposed the mesh in that window.
    // Re-check before framing so centerViewOnSelection never reads freed geometry.
    if (!meshIsLive(mesh)) return;
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
