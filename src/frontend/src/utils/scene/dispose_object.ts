import * as THREE from "three";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

/**
 * Recursively free the GPU resources (geometries, materials, their textures) under
 * ``root``.
 *
 * three.js only releases VRAM on an explicit ``dispose()``. Detaching an object from the
 * scene graph (``Object3D.clear()`` / ``scene.remove()``) just drops JS references — the
 * renderer keeps the geometry/texture in its caches, so ``renderer.info.memory`` and GPU
 * memory stay flat. Call this on a model group BEFORE clearing/removing it so a model swap
 * or "clear" actually frees memory.
 *
 * Only per-model resources are disposed: ``CustomBatchedMesh`` disposes its own cloned
 * materials (never the shared singletons), and a ``seen`` set makes shared materials within
 * the subtree dispose exactly once. Safe to call more than once.
 */
export function disposeObject3D(root: THREE.Object3D | null | undefined): void {
    if (!root) return;
    const seenMat = new Set<THREE.Material>();

    const disposeMaterial = (m: THREE.Material) => {
        if (seenMat.has(m)) return;
        seenMat.add(m);
        // Release any textures the material references (map, normalMap, ...).
        for (const value of Object.values(m as unknown as Record<string, unknown>)) {
            if (value && (value as THREE.Texture).isTexture) {
                (value as THREE.Texture).dispose();
            }
        }
        m.dispose();
    };

    root.traverse((obj) => {
        // CustomBatchedMesh owns extra resources (edge picker mesh, cached selection
        // materials, overlay) — let it free them itself.
        if (obj instanceof CustomBatchedMesh) {
            obj.dispose();
            return;
        }
        const mesh = obj as THREE.Mesh;
        const geom = (mesh as { geometry?: THREE.BufferGeometry }).geometry;
        if (geom && typeof geom.dispose === "function") {
            geom.dispose();
        }
        const mat = (mesh as { material?: THREE.Material | THREE.Material[] }).material;
        if (Array.isArray(mat)) {
            for (const m of mat) if (m) disposeMaterial(m);
        } else if (mat) {
            disposeMaterial(mat);
        }
    });
}
