// The three.js viewport behind the viewport interface: each call is what the command or key did before the
// interface existed, moved here unchanged. The modules are imported on first use, as the commands
// imported them, so the boot path does not pay for the scene helpers.
import type * as THREE from "three";
import type CameraControls from "camera-controls";
import type {OrbitControls} from "three/examples/jsm/controls/OrbitControls";

import {type CameraView} from "@/layout/coreCommandIds";
import {useModelState} from "@/state/modelState";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {batchedMeshOf, isBatched} from "@/utils/scene/meshLookup";
import {createThreeResultMesh} from "@/utils/scene/fea/threeResultLayer";
import {recordViewerAction} from "@/utils/scene/viewerHistory";
import {rangesCentroidWorld} from "@/utils/viewer/pipeTrace";
import type {BillboardLayer} from "./billboards";
import {createThreeBillboardLayer} from "./threeBillboards";
import type {ObjectRef, ObjectVisibilityReader, SetSelectionOptions, ViewportApi} from "./viewportApi";

/** The GLB node that draws `ref`: the caller's, or the Scene tree row's. */
const nodeOf = (ref: ObjectRef): string | null | undefined =>
    ref.node !== undefined ? ref.node : useTreeViewStore.getState().findNodeByRangeId(ref.model, ref.id)?.node_name;

/** `refs` as draw ranges, grouped by the mesh that draws them. */
function rangesOf(refs: readonly ObjectRef[]): Map<CustomBatchedMesh, Set<string>> {
    const out = new Map<CustomBatchedMesh, Set<string>>();
    const cache = new Map<string, CustomBatchedMesh>();
    for (const ref of refs) {
        const node = nodeOf(ref);
        const mesh = node ? batchedMeshOf(ref.model, node, cache) : null;
        if (!mesh) continue;
        const ids = out.get(mesh) ?? new Set<string>();
        ids.add(ref.id);
        out.set(mesh, ids);
    }
    return out;
}

function threeVisibilityReader(): ObjectVisibilityReader {
    const cache = new Map<string, CustomBatchedMesh>();
    return (model, id, node) => {
        const name = node !== undefined ? node : useTreeViewStore.getState().findNodeByRangeId(model, id)?.node_name;
        const mesh = name ? batchedMeshOf(model, name, cache) : null;
        if (!mesh) return null;
        return mesh.getHiddenRanges().has(id) ? "hidden" : "shown";
    };
}

/** The key a mesh's ranges are known by outside the scene (a tree row's `model_key`). */
function modelKeyOf(mesh: unknown): string | undefined {
    const m = mesh as {unique_key?: string; userData?: Record<string, unknown>};
    return m.unique_key ?? (m.userData ? (m.userData["unique_hash"] as string | undefined) : undefined);
}

/** The three.js selection (meshes and their draw ranges) as object refs. */
export function threeSelectionRefs(): ObjectRef[] {
    const out: ObjectRef[] = [];
    for (const [mesh, ranges] of useSelectedObjectStore.getState().selectedObjects) {
        const model = modelKeyOf(mesh);
        if (!model) continue;
        for (const id of ranges) out.push({model, id});
    }
    return out;
}

/** Select `refs` in the three.js scene: each one's mesh is the one its GLB node names. One undo step.
 *  Returns how many draw ranges it selected. */
export function setThreeSelection(refs: readonly ObjectRef[], options: SetSelectionOptions = {}): number {
    const store = useSelectedObjectStore.getState();
    const models = getViewerRuntime().modelKeyMap.current;
    const tree = useTreeViewStore.getState();
    const batch: [THREE.Object3D, string][] = [];
    // A mesh is looked up by name once per call, not once per object: a subtree of thousands of
    // objects usually draws from a handful of meshes, and getObjectByName walks the model.
    const meshes = new Map<string, THREE.Object3D | null>();
    for (const ref of refs) {
        const meshName = ref.node !== undefined ? ref.node : tree.findNodeByRangeId(ref.model, ref.id)?.node_name;
        if (!meshName) continue;
        const key = `${ref.model}\n${meshName}`;
        let mesh = meshes.get(key);
        if (mesh === undefined) meshes.set(key, (mesh = models?.get(ref.model)?.getObjectByName(meshName) ?? null));
        if (mesh) batch.push([mesh, ref.id]);
    }
    if (options.add) {
        if (batch.length || options.within) {
            recordViewerAction("select", () => {
                if (batch.length) store.addBatchofMeshes(batch);
                options.within?.();
            });
        }
    } else if (!refs.length) {
        recordViewerAction("clear-selection", () => {
            store.clearSelectedObjects();
            options.within?.();
        });
    } else {
        recordViewerAction("select", () => {
            store.clearSelectedObjects();
            if (batch.length) store.addBatchofMeshes(batch);
            options.within?.();
        });
    }
    requestRender();
    return batch.length;
}

const visibility = () => import("@/utils/scene/visibility");
const cameraHandlers = () => import("@/components/viewer/sceneHelpers/setupCameraControlsHandlers");
const cameraKeys = () => import("@/components/viewer/sceneHelpers/cameraKeys");

export interface ThreeViewportHandles {
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    controls: CameraControls | OrbitControls;
}

/** The viewport interface over the three.js scene, camera and controls of one mounted canvas. */
export function createThreeViewport(h: ThreeViewportHandles): ViewportApi {
    const layers = new Map<string, BillboardLayer>();
    return {
        kind: "three",
        unavailable: () => null,
        selectionCount: () => {
            let n = 0;
            for (const ids of useSelectedObjectStore.getState().selectedObjects.values()) n += ids.size;
            return n;
        },
        selection: threeSelectionRefs,
        selectionToken: () => useSelectedObjectStore.getState().selectedObjects,
        onSelectionChange: (listener) =>
            useSelectedObjectStore.subscribe((s, prev) => void (s.selectedObjects !== prev.selectedObjects && listener())),
        setSelection: setThreeSelection,
        // What clicking empty space in the tree does: one undo entry, the info panel emptied.
        clearSelection: () =>
            void import("@/utils/scene/viewerHistory").then((m) => {
                m.recordViewerAction("clear-selection", () => useSelectedObjectStore.getState().clearSelectedObjects());
                useObjectInfoStore.getState().setName(null);
                useObjectInfoStore.getState().setSelectedNodeId(null);
                requestRender();
            }),
        // Each of these is one undo / redo entry, however many objects it covers.
        hide: (refs) => {
            recordViewerAction("hide", () => {
                for (const [mesh, ids] of rangesOf(refs)) mesh.hideBatchDrawRange(ids);
                // As Shift+H: the selection overlay would keep drawing what was just hidden.
                useSelectedObjectStore.getState().clearSelectedObjects();
            });
            requestRender();
        },
        show: (refs) => {
            recordViewerAction("show", () => {
                for (const [mesh, ids] of rangesOf(refs)) mesh.unhideBatchDrawRange(ids);
            });
            requestRender();
        },
        isolate: (refs) => {
            const scene = getViewerRuntime().scene.current ?? h.scene;
            if (!scene) return;
            const keep = rangesOf(refs);
            recordViewerAction("isolate", () => {
                scene.traverse((obj) => {
                    if (isBatched(obj)) obj.isolateDrawRanges(keep.get(obj) ?? new Set());
                });
            });
            requestRender();
        },
        objectVisibility: threeVisibilityReader,
        saveScreenshot: () => void import("@/utils/takeScreenshot").then((m) => m.takeScreenshot()),
        modelBounds: () => {
            const box = useModelState.getState().boundingBox;
            return box && !box.isEmpty() ? [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z] : null;
        },
        // A result mesh from typed arrays, added to the scene. The streaming loader's own result
        // mesh comes in as a GLB (with its selection and overlays) and is wrapped by the same layer
        // (threeResultLayer); replacing the model is that loader's job here, so `replaceModel` only
        // frames the new mesh.
        results: {
            createResultMesh: (data, opts) => {
                const layer = createThreeResultMesh(data, getViewerRuntime().scene.current ?? h.scene);
                if (opts?.replaceModel) {
                    void cameraHandlers().then((m) => {
                        m.fitAllView(h.scene, h.camera, h.controls);
                        requestRender();
                    });
                }
                requestRender();
                return layer;
            },
        },
        hideSelected: () => void visibility().then((m) => m.hideSelectedRanges()),
        hideOthers: () => void visibility().then((m) => m.hideUnselectedRanges()),
        showAll: () => void visibility().then((m) => m.unhideAllRanges()),
        fitAll: () =>
            void cameraHandlers().then((m) => {
                m.fitAllView(h.scene, h.camera, h.controls);
                requestRender();
            }),
        fitSelection: () =>
            void cameraHandlers().then((m) => {
                m.fitSelectionView(h.camera, h.controls);
                requestRender();
            }),
        setView: (view: CameraView) =>
            void import("@/components/viewer/sceneHelpers/namedViews").then((m) => {
                m.applyNamedView(view, h.scene, h.camera, h.controls, useModelState.getState().zIsUp);
                requestRender();
            }),
        resetCamera: () => void cameraHandlers().then((m) => m.resetCameraHome(h.camera, h.controls)),
        orbitStep: (right, up) =>
            void cameraKeys().then((m) => {
                m.orbitByStep(h.controls, h.camera, right, up);
                requestRender();
            }),
        panStep: (right, up) =>
            void cameraKeys().then((m) => {
                m.panByStep(h.controls, h.camera, right, up);
                requestRender();
            }),
        objectsCentre: (refs) => {
            const c = rangesCentroidWorld(refs.map((ref) => [ref.model, ref.id] as const));
            return c ? [c.x, c.y, c.z] : null;
        },
        billboards: (name) => {
            let layer = layers.get(name);
            if (!layer) {
                const made = createThreeBillboardLayer(h.scene, name);
                layer = {...made, dispose: () => (layers.delete(name), made.dispose())};
                layers.set(name, layer);
            }
            return layer;
        },
    };
}
