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
import type {ObjectRef, ViewportApi} from "./viewportApi";

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

/** Select `refs` in the three.js scene: each one's mesh is the one its tree row names. One undo step. */
export async function setThreeSelection(refs: readonly ObjectRef[]): Promise<void> {
    const {recordViewerAction} = await import("@/utils/scene/viewerHistory");
    const store = useSelectedObjectStore.getState();
    if (!refs.length) {
        recordViewerAction("clear-selection", () => store.clearSelectedObjects());
        requestRender();
        return;
    }
    const models = getViewerRuntime().modelKeyMap.current;
    const tree = useTreeViewStore.getState();
    const batch: [THREE.Object3D, string][] = [];
    // A mesh is looked up by name once per call, not once per object: a subtree of thousands of
    // objects usually draws from a handful of meshes, and getObjectByName walks the model.
    const meshes = new Map<string, THREE.Object3D | null>();
    for (const ref of refs) {
        const meshName = tree.findNodeByRangeId(ref.model, ref.id)?.node_name;
        if (!meshName) continue;
        const key = `${ref.model}\n${meshName}`;
        let mesh = meshes.get(key);
        if (mesh === undefined) meshes.set(key, (mesh = models?.get(ref.model)?.getObjectByName(meshName) ?? null));
        if (mesh) batch.push([mesh, ref.id]);
    }
    recordViewerAction("select", () => {
        store.clearSelectedObjects();
        store.addBatchofMeshes(batch);
    });
    requestRender();
}

export interface ThreeViewportHandles {
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    controls: CameraControls | OrbitControls;
}

const visibility = () => import("@/utils/scene/visibility");
const cameraHandlers = () => import("@/components/viewer/sceneHelpers/setupCameraControlsHandlers");
const cameraKeys = () => import("@/components/viewer/sceneHelpers/cameraKeys");

/** The viewport interface over the three.js scene, camera and controls of one mounted canvas. */
export function createThreeViewport(h: ThreeViewportHandles): ViewportApi {
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
        setSelection: setThreeSelection,
        // What clicking empty space in the tree does: one undo entry, the info panel emptied.
        clearSelection: () =>
            void import("@/utils/scene/viewerHistory").then((m) => {
                m.recordViewerAction("clear-selection", () => useSelectedObjectStore.getState().clearSelectedObjects());
                useObjectInfoStore.getState().setName(null);
                useObjectInfoStore.getState().setSelectedNodeId(null);
                requestRender();
            }),
        modelBounds: () => {
            const box = useModelState.getState().boundingBox;
            return box && !box.isEmpty() ? [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z] : null;
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
    };
}
