// The scene-graph handles one mounted viewer holds: its THREE scene, the
// camera and controls looking at it, the renderer drawing it, and the few
// per-model handles hung off the same load.
//
// These used to be ten `createRef()` calls at module scope in `state/refs.ts`,
// imported directly by 43 files. That is a single set of pointers for the whole
// JS realm, which is exactly one viewer's worth: mount a second
// `<adapy-viewer>` on the page — a docs page embedding N mode shapes, a
// side-by-side comparison — and the second canvas overwrites the first one's
// scene, camera and renderer on mount. Every consumer then draws into, picks
// against and tears down the wrong canvas, with nothing to name the collision.
//
// A `ViewerRuntime` is one viewer's set. `AdaViewerProvider` creates one per
// mount and hands it to React consumers through `useViewerRefs()`; the
// imperative modules that have no React tree to read a context from (scene
// utilities, click handlers, the websocket message handlers) reach the mounted
// one through `getViewerRuntime()`, mirroring how the equally imperative FEA
// and model-load paths reach the open model through
// `useModelSessionStore.getState().current()`.
//
// WHAT LIVES HERE VS ON `ModelSession`
// ------------------------------------
// The canvas-lifetime handles (scene / camera / controls / renderer /
// updateLight / selectedPoint) are the runtime's by construction: they are made
// when the canvas mounts and die with it, and no model owns them.
//
// The four model-derived handles (adaExtension, simulationData,
// animationController, modelKeyMap) read like `ModelSession` state and are NOT
// here by preference — they are here because of load ordering.
// `setupModelLoaderAsync` fills all four WHILE the GLB is being parsed, and the
// session for that model is opened afterwards, by the `setLoadedSourceName`
// its caller runs on success (`sceneHelpers/loadModel`). Since
// `ModelSessionState.open()` deliberately drops the previous session's
// contents, moving them would have the session opening for a model wipe the
// handles that same model's load had just produced. Giving them their proper
// home means opening the session BEFORE the load rather than after it, which
// is a change to the load pipeline, not to where a pointer is stored.

import { createRef, type RefObject } from "react";
import type CameraControls from "camera-controls";
import type * as THREE from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls";

import type { AnimationController } from "../utils/scene/animations/AnimationController";
import type {
    ADADesignAndAnalysisExtension,
    SimulationDataExtensionMetadata,
} from "../extensions/design_and_analysis_extension";

/**
 * One mounted viewer's scene-graph handles. Field names are the old global
 * names minus the `Ref` suffix (`sceneRef` -> `scene`), and every field is
 * still a `RefObject`, so a migrated call site reads and writes `.current`
 * exactly as it did.
 */
export interface ViewerRuntime {
    /** The root THREE scene every model, overlay and helper is added to. */
    scene: RefObject<THREE.Scene | null>;
    /** The camera the canvas renders from. */
    camera: RefObject<THREE.PerspectiveCamera | null>;
    /** Whichever orbit implementation is active for this canvas. */
    controls: RefObject<CameraControls | OrbitControls | null>;
    /** The WebGL renderer owning this canvas' GPU resources. */
    renderer: RefObject<THREE.WebGLRenderer | null>;
    /** Re-aims the camera-following light; set by the canvas, called after
     *  anything that moves the camera outside the controls' own events. */
    updateLight: RefObject<(() => void) | null>;
    /** Drives the loaded GLB's animation clips. Rebuilt per model load. */
    animationController: RefObject<AnimationController | null>;
    /** The simulation-object metadata currently on show — seeded from the
     *  loaded GLB's ADA extension, replaced when a click lands on a mesh
     *  carrying its own. */
    simulationData: RefObject<SimulationDataExtensionMetadata | null>;
    /** The loaded GLB's raw ADA design-and-analysis extension. */
    adaExtension: RefObject<ADADesignAndAnalysisExtension | null>;
    /** Model hash -> the group it was added under, for the tree and the
     *  unload paths. */
    modelKeyMap: RefObject<Map<string, THREE.Object3D | THREE.Group> | null>;
    /** Highlight overlay for a single selected point. */
    selectedPoint: RefObject<THREE.Points | null>;
}

/** A fresh, empty runtime. One per `<AdaViewerProvider>` mount. */
export function createViewerRuntime(): ViewerRuntime {
    return {
        scene: createRef<THREE.Scene | null>(),
        camera: createRef<THREE.PerspectiveCamera | null>(),
        controls: createRef<CameraControls | OrbitControls | null>(),
        renderer: createRef<THREE.WebGLRenderer | null>(),
        updateLight: createRef<() => void>(),
        animationController: createRef<AnimationController | null>(),
        simulationData: createRef<SimulationDataExtensionMetadata | null>(),
        adaExtension: createRef<ADADesignAndAnalysisExtension | null>(),
        modelKeyMap: createRef<Map<string, THREE.Object3D | THREE.Group> | null>(),
        selectedPoint: createRef<THREE.Points | null>(),
    };
}

// Mounted runtimes, in mount order. A stack rather than a single slot so that
// unmounting the newer of two viewers hands imperative callers back to the one
// still on the page, instead of to nothing.
const mounted: ViewerRuntime[] = [];

// The runtime imperative callers get when no provider is mounted: module-scope
// code, a unit test importing a scene utility, the brief window before the app
// renders. Created on first use so importing this module costs nothing, and
// kept for the realm's lifetime so two such callers agree with each other.
let detached: ViewerRuntime | null = null;

/**
 * The runtime imperative modules should act on: the most recently mounted
 * viewer, or a detached one when nothing is mounted.
 *
 * The React counterpart is `useViewerRefs()`, which gives a component the
 * runtime of the provider it is actually inside. Prefer it in components: with
 * two viewers on the page this function can only answer for one of them, and
 * the hook is what makes the other one addressable.
 */
export function getViewerRuntime(): ViewerRuntime {
    return mounted[mounted.length - 1] ?? (detached ??= createViewerRuntime());
}

/** True while at least one `<AdaViewerProvider>` is mounted. */
export function hasMountedViewerRuntime(): boolean {
    return mounted.length > 0;
}

/**
 * Register `runtime` as mounted and return the matching unregister. Called by
 * `AdaViewerProvider`; idempotent, so a re-entered render cannot stack the same
 * runtime twice.
 */
export function mountViewerRuntime(runtime: ViewerRuntime): () => void {
    if (!mounted.includes(runtime)) mounted.push(runtime);
    return () => {
        const at = mounted.indexOf(runtime);
        if (at >= 0) mounted.splice(at, 1);
    };
}
