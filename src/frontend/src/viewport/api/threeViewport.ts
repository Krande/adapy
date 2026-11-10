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
import type {ViewportApi} from "./viewportApi";

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
        // What clicking empty space in the tree does: one undo entry, the info panel emptied.
        clearSelection: () =>
            void import("@/utils/scene/viewerHistory").then((m) => {
                m.recordViewerAction("clear-selection", () => useSelectedObjectStore.getState().clearSelectedObjects());
                useObjectInfoStore.getState().setName(null);
                useObjectInfoStore.getState().setSelectedNodeId(null);
                requestRender();
            }),
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
