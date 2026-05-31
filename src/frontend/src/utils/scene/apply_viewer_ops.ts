// Apply a viewer-ops payload (returned by a worker @utility, e.g. diff) to the
// live scene: recolour elements in place and/or add overlay geometry (elements
// present in a compare-ref but absent from the loaded model). `clearViewerOps`
// resets the scene to its original look.
import * as THREE from "three";

import {sceneRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {loadGLTF} from "@/components/viewer/sceneHelpers/asyncModelLoader";
import {viewerApi, type ScopeUrl} from "@/services/viewerApi";

export interface ColorElement {
    key: string;        // element rangeId (== GLB draw-range / node_id)
    color: string;      // #rrggbb
}

export interface ViewerOp {
    op: "color_elements" | "add_overlay_geometry";
    elements?: ColorElement[];
    blob_key?: string;
    label?: string;
    color?: string;
}

export interface ViewerOpsPayload {
    version?: number;
    ops: ViewerOp[];
    legend?: {label: string; color: string; count?: number}[];
    summary?: Record<string, unknown>;
}

// Overlay layers added by the last utility run, tracked so `clearViewerOps`
// can remove them. Keyed by the THREE node added to the scene.
const _overlayGroups: THREE.Object3D[] = [];

function _eachBatchedMesh(fn: (m: CustomBatchedMesh) => void): void {
    sceneRef.current?.traverse((obj) => {
        if (obj instanceof CustomBatchedMesh) fn(obj);
    });
}

function _applyColorElements(elements: ColorElement[]): void {
    // Index colours by rangeId once; each mesh picks the keys it owns.
    const byKey = new Map<string, THREE.Color>();
    for (const e of elements) byKey.set(String(e.key), new THREE.Color(e.color));
    _eachBatchedMesh((mesh) => {
        const local = new Map<string, THREE.Color>();
        for (const rangeId of mesh.drawRanges.keys()) {
            const c = byKey.get(rangeId);
            if (c) local.set(rangeId, c);
        }
        if (local.size) mesh.setRangeColors(local);
    });
}

async function _addOverlay(op: ViewerOp, scope: ScopeUrl): Promise<void> {
    if (!op.blob_key || !sceneRef.current) return;
    const buf = await viewerApi.getBlob(scope, op.blob_key);
    const url = URL.createObjectURL(new Blob([buf], {type: "model/gltf-binary"}));
    try {
        const gltf = await loadGLTF(url);
        const group = gltf.scene;
        group.name = `__diff_overlay__:${op.label ?? op.blob_key}`;
        // The overlay GLB carries its own (red) material; keep it as-is.
        sceneRef.current.add(group);
        _overlayGroups.push(group);
    } finally {
        URL.revokeObjectURL(url);
    }
}

/** Apply a viewer-ops payload to the scene. Resets any previous ops first. */
export async function applyViewerOps(payload: ViewerOpsPayload, scope: ScopeUrl): Promise<void> {
    clearViewerOps();
    for (const op of payload.ops || []) {
        if (op.op === "color_elements" && op.elements) {
            _applyColorElements(op.elements);
        } else if (op.op === "add_overlay_geometry") {
            await _addOverlay(op, scope);
        }
    }
    requestRender();
}

/** Reset the scene to its original look: restore element colours and remove
 *  overlay layers. Backs the panel's "Reset scene" button. */
export function clearViewerOps(): void {
    _eachBatchedMesh((mesh) => mesh.disableVertexColorsAndResetMaterial());
    for (const group of _overlayGroups.splice(0)) {
        group.parent?.remove(group);
        group.traverse((o) => {
            const m = o as THREE.Mesh;
            if (m.geometry) m.geometry.dispose?.();
            const mat = m.material as THREE.Material | THREE.Material[] | undefined;
            if (Array.isArray(mat)) mat.forEach((x) => x.dispose?.());
            else mat?.dispose?.();
        });
    }
    requestRender();
}
