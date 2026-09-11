// state/refs.ts — DEPRECATED, being deleted.
//
// Every handle here now belongs to a `ViewerRuntime` (`state/viewerRuntime`),
// one per mounted `<AdaViewerProvider>`. What is left is a live view onto the
// mounted runtime, so the call sites still importing these names keep working
// while they are migrated:
//
//   components        `const {scene} = useViewerRefs()`
//   imperative util   `getViewerRuntime().scene.current`
//
// Nothing new should import this file.

import type CameraControls from "camera-controls";
import type { RefObject } from "react";
import type * as THREE from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls";
import type { AnimationController } from "../utils/scene/animations/AnimationController";
import type {
    ADADesignAndAnalysisExtension,
    SimulationDataExtensionMetadata,
} from "../extensions/design_and_analysis_extension";

import { getViewerRuntime, type ViewerRuntime } from "./viewerRuntime";

/** A `RefObject` façade that reads and writes the mounted runtime's field. */
function live<K extends keyof ViewerRuntime>(key: K): ViewerRuntime[K] {
    return {
        get current() {
            return getViewerRuntime()[key].current;
        },
        set current(value) {
            (getViewerRuntime()[key] as { current: unknown }).current = value;
        },
    } as ViewerRuntime[K];
}

/** @deprecated use `useViewerRefs().camera` / `getViewerRuntime().camera` */
export const cameraRef: RefObject<THREE.PerspectiveCamera | null> = live("camera");
/** @deprecated use `useViewerRefs().controls` / `getViewerRuntime().controls` */
export const controlsRef: RefObject<CameraControls | OrbitControls | null> = live("controls");
/** @deprecated use `useViewerRefs().renderer` / `getViewerRuntime().renderer` */
export const rendererRef: RefObject<THREE.WebGLRenderer | null> = live("renderer");
/** @deprecated use `useViewerRefs().scene` / `getViewerRuntime().scene` */
export const sceneRef: RefObject<THREE.Scene | null> = live("scene");
/** @deprecated use `useViewerRefs().updateLight` / `getViewerRuntime().updateLight` */
export const updatelightRef: RefObject<(() => void) | null> = live("updateLight");
/** @deprecated use `useViewerRefs().animationController` */
export const animationControllerRef: RefObject<AnimationController | null> =
    live("animationController");
/** @deprecated use `useViewerRefs().simulationData` */
export const simulationDataRef: RefObject<SimulationDataExtensionMetadata | null> =
    live("simulationData");
/** @deprecated use `useViewerRefs().adaExtension` */
export const adaExtensionRef: RefObject<ADADesignAndAnalysisExtension | null> =
    live("adaExtension");
/** @deprecated use `useViewerRefs().modelKeyMap` */
export const modelKeyMapRef: RefObject<Map<string, THREE.Object3D | THREE.Group> | null> =
    live("modelKeyMap");
/** @deprecated use `useViewerRefs().selectedPoint` */
export const selectedPointRef: RefObject<THREE.Points | null> = live("selectedPoint");
