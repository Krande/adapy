// The three.js mesh that draws a model's GLB node, by name. One place for the lookup the Scene tree's
// actions and the three.js viewport both need.
import {getViewerRuntime} from "@/state/viewerRuntime";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

/** A batched mesh, by what it can do rather than `instanceof`: a hot reload can leave the scene
 *  holding meshes of an earlier copy of the class. */
export function isBatched(o: unknown): o is CustomBatchedMesh {
    return !!o && typeof (o as CustomBatchedMesh).getHiddenRanges === "function" && typeof (o as CustomBatchedMesh).hideBatchDrawRange === "function";
}

/** The batched mesh named `node` in the model loaded as `model`, or null. `cache` saves the
 *  scene-graph walk across many lookups; a lookup that finds nothing is not cached, since the mesh
 *  may simply not have arrived yet. */
export function batchedMeshOf(model: string, node: string, cache: Map<string, CustomBatchedMesh>): CustomBatchedMesh | null {
    const key = `${model}\u0000${node}`;
    const hit = cache.get(key);
    if (hit) return hit;
    const found = getViewerRuntime().modelKeyMap.current?.get(model)?.getObjectByName(node);
    // The name can resolve to a wrapper around the mesh rather than the mesh itself (selection
    // entries are keyed either way; see hideSelectedRanges), so look inside it too.
    let mesh: CustomBatchedMesh | null = isBatched(found) ? found : null;
    if (!mesh && found) {
        found.traverse((o) => {
            if (!mesh && isBatched(o)) mesh = o;
        });
    }
    if (!mesh) return null;
    cache.set(key, mesh);
    return mesh;
}
