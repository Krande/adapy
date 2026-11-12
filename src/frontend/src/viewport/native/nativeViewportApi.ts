// The three.js-free viewport behind the viewport interface (viewport/api/viewportApi.ts).
import {type CameraView} from "@/layout/coreCommandIds";
import {namedViewDirection} from "@/utils/camera/namedViewDirection";
import {downloadCanvas} from "@/utils/downloadCanvas";
import {matrixProjector} from "@/utils/scene/fea/labelPlacement";
import type {ObjectRef, ViewportApi} from "@/viewport/api/viewportApi";
import type {BillboardLayer} from "@/viewport/api/billboards";
import {createNativeBillboardLayer} from "./nativeBillboards";
import type {OverlayLayer} from "@/viewport/api/overlayLayer";
import {createNativeOverlayLayer} from "./nativeOverlays";
import type {ImageLayer} from "@/viewport/api/imageLayer";
import {createNativeImageLayer} from "./nativeImages";
import {KeySet, nativeObjectsInBox, nativeObjectsInScreenRect, nativeSelectionBounds} from "./nativeBoxSelection";
import {objectKey, splitObjectKey, type NativeScene} from "./nativeScene";
import {withNativeHistory} from "./nativeViewportHistory";
import {hexLinear} from "@/state/appearanceStore";
import {parseHexColor} from "@/viewport/api/objectColors";

/** One key press orbits by this much, and pans by this fraction of the view: as on the three.js viewport. */
const ORBIT_STEP_RAD = (15 * Math.PI) / 180;
const PAN_STEP_FRACTION = 0.1;
const MOVE_SECONDS = 0.25;
/** A screenshot's size, as the three.js viewport's. */
const SCREENSHOT_WIDTH = 1920;
const SCREENSHOT_HEIGHT = 1080;

export function createNativeViewportApi(scene: NativeScene): ViewportApi {
    const layers = new Map<string, BillboardLayer>();
    const overlayLayers = new Map<string, OverlayLayer>();
    const imageLayers = new Map<string, ImageLayer>();
    // The scene knows an object by one key made of its model and its id.
    const own = (refs: Iterable<ObjectRef>): string[] => Array.from(refs, (r) => objectKey(r.model, r.id));
    // Each call that changes the selection or what is hidden is one undo step (./nativeViewportHistory.ts).
    return withNativeHistory(scene, {
        kind: "native",
        unavailable: () => null,
        selectionCount: () => scene.selectionSize,
        selection: () => scene.selectedIds.map(splitObjectKey),
        selectionToken: () => scene.selectionVersion,
        onSelectionChange: (listener) => {
            scene.onSelection.add(listener);
            return () => void scene.onSelection.delete(listener);
        },
        setSelection: (refs, options) => {
            // What this viewport found itself (a box's objects) already has the scene's keys.
            const selected = scene.setSelection(refs instanceof KeySet ? refs.keys : own(refs), options?.add);
            options?.within?.();
            return selected;
        },
        clearSelection: () => scene.select(null),
        hide: (refs) => scene.setHiddenMany(own(refs), true),
        show: (refs) => scene.setHiddenMany(own(refs), false),
        isolate: (refs) => scene.isolate(own(refs)),
        // A colour per object, drawn as the colour of its segments in the renderer (NativeScene).
        setObjectColors: (refs, color) => {
            if (color !== null && parseHexColor(color) === null) return 0;
            return scene.setObjectColors(refs, color === null ? null : hexLinear(color));
        },
        objectColorCount: () => scene.objectColorCount,
        captureObjectColors: () => scene.captureObjectColors(),
        restoreObjectColors: (state) => scene.restoreObjectColors(state),
        results: {createResultMesh: (data, opts) => scene.createResultLayer(data, opts)},
        objectVisibility: () => (model, id) => {
            const key = objectKey(model, id);
            return scene.draws(key) ? (scene.isHidden(key) ? "hidden" : "shown") : null;
        },
        // Every model on screen is placed by the scene's one offset (viewport/api/sceneFrame).
        sourceOrigin: (sourceName) => (scene.hasSource(sourceName) ? scene.sceneOffset : null),
        unloadSource: (sourceName) => scene.unloadSource(sourceName),
        clearModels: () => scene.clearModels(),
        modelBounds: () => scene.modelBounds,
        saveScreenshot: () => {
            const picture = scene.capture(SCREENSHOT_WIDTH, SCREENSHOT_HEIGHT);
            if (picture) downloadCanvas(picture, "screenshot.png");
        },
        hideSelected: () => scene.hideSelected(),
        hideOthers: () => scene.hideOthers(),
        showAll: () => scene.showAll(),
        fitAll: () => scene.fit(MOVE_SECONDS),
        fitSelection: () => void scene.fitSelection(),
        setView: (view: CameraView) => scene.viewFrom(namedViewDirection(view)),
        resetCamera: () => scene.home(),
        cameraPose: () => scene.viewPose(),
        setCameraPose: (eye, target, roll) => scene.setCameraPose(eye, target, roll),
        orbitStep: (right, up) => scene.orbitBy(right * ORBIT_STEP_RAD, up * ORBIT_STEP_RAD),
        panStep: (right, up) => scene.panBy(right * PAN_STEP_FRACTION, up * PAN_STEP_FRACTION),
        objectsCentre: (refs) => scene.objectsCentre(own(refs)),
        billboards: (name) => {
            let layer = layers.get(name);
            if (!layer) {
                const made = createNativeBillboardLayer(scene);
                layer = {...made, dispose: () => (layers.delete(name), made.dispose())};
                layers.set(name, layer);
            }
            return layer;
        },
        overlays: (name) => {
            let layer = overlayLayers.get(name);
            if (!layer) {
                const made = createNativeOverlayLayer(scene);
                layer = {...made, dispose: () => (overlayLayers.delete(name), made.dispose())};
                overlayLayers.set(name, layer);
            }
            return layer;
        },
        images: (name) => {
            let layer = imageLayers.get(name);
            if (!layer) {
                const made = createNativeImageLayer(scene);
                layer = {...made, dispose: () => (imageLayers.delete(name), made.dispose())};
                imageLayers.set(name, layer);
            }
            return layer;
        },
        // The renderer places the animated nodes of the loaded GLBs by time (NativeScene).
        modelAnimation: () => scene.modelAnimation,
        setOrbitPivot: (point) => scene.setOrbitPivot(point),
        fitBox: (min, max) => scene.fitBox([min[0], min[1], min[2], max[0], max[1], max[2]]),
        // Box selection, decided on each object's box (./nativeBoxSelection.ts says where that is coarser).
        objectsInBox: (box, mode) => nativeObjectsInBox(scene, box, mode),
        objectsInScreenRect: (rect, mode) => nativeObjectsInScreenRect(scene, rect, mode),
        selectionBounds: () => nativeSelectionBounds(scene),
        projectToScreen: (world, count, out) => {
            const {view, proj, width, height} = scene.screenProjection();
            return matrixProjector(view, proj, width, height, 0)(world, count, out);
        },
        // A frame is drawn only when something changed, the camera included: every frame counts.
        onCameraChange: (listener) => {
            scene.onFrame.add(listener);
            return () => void scene.onFrame.delete(listener);
        },
    });
}
