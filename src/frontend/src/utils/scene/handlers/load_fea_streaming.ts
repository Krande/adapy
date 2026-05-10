import * as THREE from "three";

import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import {runtime} from "@/runtime/config";
import {fetchFieldBlob} from "@/services/feaFieldBlob";
import {fetchMeshEdges} from "@/services/feaMeshEdges";
import {FeaManifest, FeaManifestField, viewerApi} from "@/services/viewerApi";
import {sceneRef} from "@/state/refs";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {applyFieldToMesh} from "../fea/applyField";
import {replace_model} from "./update_scene_from_message";

// Cached state for the currently-rendered FEA streaming source.
// Lets the picker re-apply with a different (component, step) on
// slider drag without re-fetching the mesh GLB or the field blob —
// switching steps within a single field becomes a synchronous
// in-memory operation.
interface ActiveFeaStreaming {
    sourceName: string;
    manifest: FeaManifest;
    /** The THREE mesh whose geometry we deform. */
    mesh: THREE.Mesh;
    /** Snapshot of the mesh's original positions, used to compute
     * displacement-from-base on every step change. */
    basePositions: Float32Array;
}

let active: ActiveFeaStreaming | null = null;

/** Drop the cached state on next call (e.g. when the user replaces
 * the scene with a different file). The blob cache lives separately
 * in feaFieldBlob.ts. */
export function clearActiveFeaStreaming(): void {
    active = null;
}

function findFirstMesh(root: THREE.Object3D): THREE.Mesh | null {
    let found: THREE.Mesh | null = null;
    root.traverse((obj) => {
        if (found) return;
        if ((obj as THREE.Mesh).isMesh) {
            found = obj as THREE.Mesh;
        }
    });
    return found;
}

function snapshotBasePositions(geometry: THREE.BufferGeometry): Float32Array {
    const attr = geometry.getAttribute("position");
    if (!attr || attr.itemSize !== 3) {
        throw new Error("FEA mesh GLB has no usable position attribute");
    }
    return new Float32Array(attr.array as Float32Array);
}

/** Load the mesh GLB, fetch the chosen field's blob, and apply the
 * (component, step) selection. Subsequent calls for the same source
 * + field skip the network and just swap the step. */
export async function load_fea_streaming(args: {
    sourceName: string;
    manifest: FeaManifest;
    fieldName: string;
    stepIndex: number;
    reduction: string;
    displacementScale?: number;
}): Promise<void> {
    if (!runtime.isRestMode()) {
        throw new Error("FEA streaming viewer is only available in REST mode");
    }
    const {sourceName, manifest, fieldName, stepIndex, reduction} = args;
    const displacementScale = args.displacementScale ?? 1;

    const field = manifest.fields.find((f) => f.name_canonical === fieldName);
    if (!field) {
        throw new Error(`field ${fieldName!} not found in manifest`);
    }
    if (stepIndex < 0 || stepIndex >= field.n_steps) {
        throw new Error(
            `step index ${stepIndex} out of range (0..${field.n_steps - 1})`,
        );
    }

    const scope = scopeUrlPart(useScopeStore.getState().current);

    // (Re-)load the mesh into the scene if we don't already have it
    // for this source. Switching field-within-source keeps the same
    // mesh; switching source forces a reload.
    if (!active || active.sourceName !== sourceName) {
        const meshKey = `_derived/${sourceName.replace(/^\/+/, "")}.fea/${manifest.mesh.url}`;
        const buf = await viewerApi.getBlob(scope, meshKey);
        const blob = new Blob([buf], {type: "model/gltf-binary"});
        const url = URL.createObjectURL(blob);
        try {
            await replace_model(url);
            const ms = useModelState.getState();
            ms.setModelUrl(url, SceneOperations.REPLACE);
            ms.setLoadedSourceName(sourceName);
        } catch (err) {
            URL.revokeObjectURL(url);
            throw err;
        }

        const scene = sceneRef.current;
        if (!scene) throw new Error("scene not ready");
        const mesh = findFirstMesh(scene);
        if (!mesh) throw new Error("loaded GLB has no mesh");
        const basePositions = snapshotBasePositions(mesh.geometry);

        active = {sourceName, manifest, mesh, basePositions};

        // Make sure the material renders vertex colours. The mesh
        // GLB doesn't ship colours; we install them below.
        const mat = mesh.material as THREE.MeshStandardMaterial;
        if (mat && "vertexColors" in mat) {
            mat.vertexColors = true;
            mat.needsUpdate = true;
        }

        // Element-edge wireframe overlay. The bake emits an explicit
        // edge sidecar (deduped uint32 pairs from each cell's
        // ElemShape.edges) so the wireframe shows real element
        // boundaries — not the diagonals from quad-face triangulation.
        // Sharing the mesh's position attribute means deformation
        // updates both face and line rendering from a single buffer.
        if (manifest.mesh.edges_url) {
            try {
                const edgeIndices = await fetchMeshEdges(
                    scope,
                    sourceName,
                    manifest.mesh.edges_url,
                );
                if (edgeIndices.length > 0) {
                    const lineGeom = new THREE.BufferGeometry();
                    lineGeom.setAttribute("position", mesh.geometry.attributes.position);
                    lineGeom.setIndex(new THREE.BufferAttribute(edgeIndices, 1));
                    const lineMat = new THREE.LineBasicMaterial({
                        color: 0x111111,
                        depthTest: true,
                    });
                    const segments = new THREE.LineSegments(lineGeom, lineMat);
                    segments.name = "fea-element-edges";
                    mesh.add(segments);
                }
            } catch (err) {
                // Wireframe overlay is decorative — log and continue
                // so a missing/corrupt sidecar doesn't block rendering.
                // eslint-disable-next-line no-console
                console.warn("[fea-streaming] failed to load mesh edges:", err);
            }
        }
    }

    const parsed = await fetchFieldBlob(scope, sourceName, field);
    const stepValues = parsed.steps[stepIndex];

    applyFieldToMesh({
        geometry: active.mesh.geometry,
        basePositions: active.basePositions,
        stepValues,
        field,
        reduction,
        displacementScale,
    });
}
