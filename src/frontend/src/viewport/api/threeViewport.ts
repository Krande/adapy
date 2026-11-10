// The three.js viewport behind the viewport interface: each call is what the command or key did before the
// interface existed, moved here unchanged. The modules are imported on first use, as the commands
// imported them, so the boot path does not pay for the scene helpers.
import type * as THREE from "three";
import {Box3, Vector3} from "three";
import type CameraControls from "camera-controls";
import type {OrbitControls} from "three/examples/jsm/controls/OrbitControls";

import {type CameraView} from "@/layout/coreCommandIds";
import {loadedSourceGroups, useModelState} from "@/state/modelState";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {batchedMeshOf, isBatched, sourceSpaceRoot} from "@/utils/scene/meshLookup";
import {matrixProjector} from "@/utils/scene/fea/labelPlacement";
import {createThreeResultMesh} from "@/utils/scene/fea/threeResultLayer";
import {recordViewerAction} from "@/utils/scene/viewerHistory";
import {captureThreeState, restoreThreeState, threeStateChange} from "@/utils/scene/threeViewportState";
import {RangeSet, rangesInCameraRect, rangesInWorldBox, threeSelectionBounds} from "@/utils/bboxSelection/threeBoxSelection";
import {rangesCentroidWorld} from "@/utils/viewer/pipeTrace";
import type {BillboardLayer} from "./billboards";
import {createThreeBillboardLayer} from "./threeBillboards";
import type {OverlayLayer} from "./overlayLayer";
import {createThreeOverlayLayer} from "./threeOverlays";
import {frameBox} from "@/components/viewer/sceneHelpers/cameraFit";
import type {ObjectRef, ObjectVisibilityReader, SetSelectionOptions, ViewportApi} from "./viewportApi";

/** The GLB node that draws `ref`: the caller's, or the Scene tree row's. */
const nodeOf = (ref: ObjectRef): string | null | undefined =>
    ref.node !== undefined ? ref.node : useTreeViewStore.getState().findNodeByRangeId(ref.model, ref.id)?.node_name;

/** `refs` as draw ranges, grouped by the mesh that draws them. */
function rangesOf(refs: Iterable<ObjectRef>): Map<CustomBatchedMesh, Set<string>> {
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
export function setThreeSelection(refs: Iterable<ObjectRef>, options: SetSelectionOptions = {}): number {
    const store = useSelectedObjectStore.getState();
    // What this viewport found itself (the ranges a box or a rectangle takes) comes grouped by mesh:
    // there is nothing to look up, and no pair is made per range.
    if (refs instanceof RangeSet) {
        recordViewerAction("select", () => {
            if (!options.add) store.clearSelectedObjects();
            if (refs.size) store.addRangesByMesh(refs.groups);
            options.within?.();
        }, {coalesce: options.coalesce});
        requestRender();
        return refs.size;
    }
    const models = getViewerRuntime().modelKeyMap.current;
    const tree = useTreeViewStore.getState();
    const batch: [THREE.Object3D, string][] = [];
    // A mesh is looked up by name once per call, not once per object: a subtree of thousands of
    // objects usually draws from a handful of meshes, and getObjectByName walks the model.
    const meshes = new Map<string, THREE.Object3D | null>();
    // Consecutive objects are nearly always drawn by the same mesh: the last answer is tried first,
    // so a run of them costs two comparisons each and no key is built.
    let lastModel: string | null = null;
    let lastNode: string | null = null;
    let lastMesh: THREE.Object3D | null = null;
    let asked = 0;
    for (const ref of refs) {
        asked++;
        const meshName = ref.node !== undefined ? ref.node : tree.findNodeByRangeId(ref.model, ref.id)?.node_name;
        if (!meshName) continue;
        if (ref.model !== lastModel || meshName !== lastNode) {
            const key = `${ref.model}\n${meshName}`;
            let mesh = meshes.get(key);
            if (mesh === undefined) meshes.set(key, (mesh = models?.get(ref.model)?.getObjectByName(meshName) ?? null));
            lastModel = ref.model;
            lastNode = meshName;
            lastMesh = mesh;
        }
        if (lastMesh) batch.push([lastMesh, ref.id]);
    }
    const record = {coalesce: options.coalesce};
    if (options.add) {
        if (batch.length || options.within) {
            recordViewerAction("select", () => {
                if (batch.length) store.addBatchofMeshes(batch);
                options.within?.();
            }, record);
        }
    } else if (!asked) {
        recordViewerAction("clear-selection", () => {
            store.clearSelectedObjects();
            options.within?.();
        }, record);
    } else {
        recordViewerAction("select", () => {
            store.clearSelectedObjects();
            if (batch.length) store.addBatchofMeshes(batch);
            options.within?.();
        }, record);
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
    const overlayLayers = new Map<string, OverlayLayer>();
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
        // Undo: the hidden ranges per mesh and the selection store, as the history noted them itself
        // before it asked the viewport (utils/scene/threeViewportState.ts).
        captureState: captureThreeState,
        stateChange: threeStateChange,
        restoreState: restoreThreeState,
        // Where the loader put the model's own frame: its source-space root, recentred with the scene.
        sourceOrigin: (sourceName) => {
            const group = loadedSourceGroups.get(sourceName);
            if (!group) return null;
            const root = sourceSpaceRoot(group);
            root.updateWorldMatrix(true, false);
            const p = root.getWorldPosition(new Vector3());
            return [p.x, p.y, p.z];
        },
        unloadSource: () => false,
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
        cameraPose: () => {
            const position = h.camera.position;
            const target = "target" in h.controls ? h.controls.target : h.controls.getTarget(new Vector3());
            const up = h.camera.up;
            return {eye: [position.x, position.y, position.z], target: [target.x, target.y, target.z], up: [up.x, up.y, up.z], fovDeg: h.camera.fov};
        },
        // The three.js viewport's own input drives its controls directly; this is for a caller that
        // only has a pose. Roll is the 3D mouse's, which keeps it itself on this viewport.
        setCameraPose: (eye, target) => {
            if ("target" in h.controls) {
                h.camera.position.set(eye[0], eye[1], eye[2]);
                h.controls.target.set(target[0], target[1], target[2]);
                h.controls.update();
            } else {
                void h.controls.setLookAt(eye[0], eye[1], eye[2], target[0], target[1], target[2], false);
            }
            requestRender();
        },
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
        // Lines and points over the model, in the scene on screen.
        overlays: (name) => {
            let layer = overlayLayers.get(name);
            if (!layer) {
                const made = createThreeOverlayLayer(() => getViewerRuntime().scene.current ?? h.scene ?? null, name);
                layer = {...made, dispose: () => (overlayLayers.delete(name), made.dispose())};
                overlayLayers.set(name, layer);
            }
            return layer;
        },
        fitBox: (min, max) => {
            const rt = getViewerRuntime();
            const camera = rt.camera.current ?? h.camera;
            const controls = rt.controls.current ?? h.controls;
            const box = new Box3(new Vector3(min[0], min[1], min[2]), new Vector3(max[0], max[1], max[2]));
            if (box.isEmpty() || !camera || !controls) return false;
            frameBox(box, camera, controls);
            rt.updateLight.current?.();
            requestRender();
            return true;
        },
        // Box selection: decided on the triangles and the cached range bounds, as before the interface.
        objectsInBox: rangesInWorldBox,
        objectsInScreenRect: (rect, mode) => rangesInCameraRect(rect, mode, h.camera),
        selectionBounds: threeSelectionBounds,
        projectToScreen: (world, count, out) => {
            const rt = getViewerRuntime();
            const camera = rt.camera.current ?? h.camera;
            const el = rt.renderer.current?.domElement;
            const width = el?.clientWidth ?? 0, height = el?.clientHeight ?? 0;
            return matrixProjector(camera.matrixWorldInverse.elements, camera.projectionMatrix.elements, width, height, -1)(world, count, out);
        },
        onCameraChange: (listener) => {
            // The camera is moved by the controls, by commands and by animations, none of which
            // says so in one place: its matrices and the view's size are compared once a frame
            // for as long as someone listens. Numbers only, nothing allocated.
            const seen = new Float64Array(34);
            let raf = 0;
            const check = () => {
                raf = requestAnimationFrame(check);
                const rt = getViewerRuntime();
                const camera = rt.camera.current ?? h.camera;
                const el = rt.renderer.current?.domElement;
                const world = camera.matrixWorld.elements, proj = camera.projectionMatrix.elements;
                let changed = false;
                for (let i = 0; i < 34; i++) {
                    const v = i < 16 ? world[i] : i < 32 ? proj[i - 16] : i === 32 ? (el?.clientWidth ?? 0) : (el?.clientHeight ?? 0);
                    if (seen[i] !== v) {
                        seen[i] = v;
                        changed = true;
                    }
                }
                if (changed) listener();
            };
            raf = requestAnimationFrame(check);
            return () => cancelAnimationFrame(raf);
        },
    };
}
