// The three.js viewport's side of measuring (viewport/api/viewportApi.ts' `surfaceAt` and
// `featureEdges`): the surface under a point of the view, and an object's feature edges, from the
// meshes the scene holds. three.js only; the three.js-free viewport has its own (NativeScene).
import * as THREE from "three";

import {getViewerRuntime} from "@/state/viewerRuntime";
import {computeEdgeArrays} from "@/utils/mesh_select/edgeCore";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {batchedMeshOf} from "@/utils/scene/meshLookup";
import {useTreeViewStore} from "@/state/treeViewStore";
import {pickMeshAt} from "@/viewport/picking";
import type {ObjectRef, SurfaceHit} from "@/viewport/api/viewportApi";

/** The outline's angle on the other viewport, and of this one's edges with tessellation edges hidden. */
const FEATURE_EDGE_ANGLE_DEG = 30;
const FEATURE_DOT = Math.cos((FEATURE_EDGE_ANGLE_DEG * Math.PI) / 180);

const refKey = (ref: ObjectRef): string => `${ref.model}\u0000${ref.id}`;

/** The meshes objects were last found on under the pointer: what `featureEdges` looks in first (an
 *  object of a result has no row in the tree to find its mesh by). */
const pickedMeshes = new Map<string, CustomBatchedMesh>();
/** Feature edges already found, by object; a few. */
const edgeCache = new Map<string, {mesh: CustomBatchedMesh; edges: Float32Array | null}>();
const KEEP = 64;

function remember<V>(map: Map<string, V>, key: string, value: V): void {
    if (map.size >= KEEP && !map.has(key)) map.delete(map.keys().next().value!);
    map.set(key, value);
}

/** The model mesh and point under a point of the view (CSS pixels from its corner): the GPU pick,
 *  then the exact point on the picked object's own triangles. */
export async function threeSurfaceAt(x: number, y: number): Promise<SurfaceHit | null> {
    const rt = getViewerRuntime();
    const renderer = rt.renderer.current;
    const camera = rt.camera.current;
    if (!renderer || !camera) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    const clientX = rect.left + x, clientY = rect.top + y;
    let pick: Awaited<ReturnType<typeof pickMeshAt>> = null;
    try {
        pick = await pickMeshAt(clientX, clientY);
    } catch {
        pick = null;
    }
    if (!pick) return null;
    const mesh = pick.mesh as CustomBatchedMesh;
    const ref: ObjectRef | null = pick.rangeId && mesh.unique_key ? {model: mesh.unique_key, id: pick.rangeId} : null;
    let world: THREE.Vector3 = pick.worldPosition;
    const range = pick.rangeId ? mesh.drawRanges?.get(pick.rangeId) : undefined;
    if (range) {
        const {raycastRangeOnMesh} = await import("@/components/viewer/sceneHelpers/setupPointerHandler");
        const refined = raycastRangeOnMesh({clientX, clientY} as MouseEvent, camera as THREE.PerspectiveCamera, renderer, mesh, range[0], range[1]);
        if (refined) world = refined.point;
    }
    if (ref) remember(pickedMeshes, refKey(ref), mesh);
    return {ref, world: [world.x, world.y, world.z]};
}

/** One object's feature edges in the world: the edges of its draw range's triangles where the faces
 *  meet at more than 30 degrees, and its boundary (utils/mesh_select/edgeCore.ts, the rule the edge
 *  overlay is built by). Null for an object no mesh draws, or one a result deforms. */
export function threeFeatureEdges(ref: ObjectRef): Float32Array | null {
    const key = refKey(ref);
    let mesh: CustomBatchedMesh | null = pickedMeshes.get(key) ?? null;
    if (!mesh) {
        const node = ref.node ?? useTreeViewStore.getState().findNodeByRangeId(ref.model, ref.id)?.node_name;
        mesh = node ? batchedMeshOf(ref.model, node, new Map()) : null;
    }
    if (!mesh) return null;
    const cached = edgeCache.get(key);
    if (cached?.mesh === mesh) return cached.edges;
    const edges = meshRangeEdges(mesh, ref.id);
    remember(edgeCache, key, {mesh, edges});
    return edges;
}

function meshRangeEdges(mesh: CustomBatchedMesh, id: string): Float32Array | null {
    const range = mesh.drawRanges?.get(id);
    const geometry = mesh.geometry as THREE.BufferGeometry | undefined;
    const position = geometry?.attributes.position as THREE.BufferAttribute | undefined;
    const index = geometry?.index;
    if (!range || !position || !index) return null;
    // A deformed result is drawn elsewhere than its positions say.
    if ((mesh.morphTargetInfluences ?? []).some((w) => w !== 0)) return null;
    const out = computeEdgeArrays(position.array as Float32Array, index.array as Uint32Array | Uint16Array, [[range[0], range[1]]], FEATURE_DOT).positions;
    mesh.updateWorldMatrix(true, false);
    const m = mesh.matrixWorld.elements;
    for (let i = 0; i + 2 < out.length; i += 3) {
        const x = out[i], y = out[i + 1], z = out[i + 2];
        out[i] = m[0] * x + m[4] * y + m[8] * z + m[12];
        out[i + 1] = m[1] * x + m[5] * y + m[9] * z + m[13];
        out[i + 2] = m[2] * x + m[6] * y + m[10] * z + m[14];
    }
    return out;
}
