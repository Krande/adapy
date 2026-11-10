// The three.js-free viewport behind the viewport interface (viewport/api/viewportApi.ts).
import {type CameraView} from "@/layout/coreCommandIds";
import {namedViewDirection} from "@/utils/camera/namedViewDirection";
import {downloadCanvas} from "@/utils/downloadCanvas";
import type {ObjectRef, ViewportApi} from "@/viewport/api/viewportApi";
import type {BillboardLayer} from "@/viewport/api/billboards";
import {createNativeBillboardLayer} from "./nativeBillboards";
import type {NativeScene} from "./nativeScene";

/** One key press orbits by this much, and pans by this fraction of the view: as on the three.js viewport. */
const ORBIT_STEP_RAD = (15 * Math.PI) / 180;
const PAN_STEP_FRACTION = 0.1;
const MOVE_SECONDS = 0.25;
/** A screenshot's size, as the three.js viewport's. */
const SCREENSHOT_WIDTH = 1920;
const SCREENSHOT_HEIGHT = 1080;

export function createNativeViewportApi(scene: NativeScene): ViewportApi {
    const layers = new Map<string, BillboardLayer>();
    const own = (refs: readonly ObjectRef[]): string[] => refs.filter((r) => r.model === scene.modelKey).map((r) => r.id);
    return {
        kind: "native",
        unavailable: () => null,
        selectionCount: () => scene.selectionSize,
        selection: () => scene.selectedIds.map((id) => ({model: scene.modelKey, id})),
        selectionToken: () => scene.selectionVersion,
        onSelectionChange: (listener) => {
            scene.onSelection.add(listener);
            return () => void scene.onSelection.delete(listener);
        },
        setSelection: (refs, options) => {
            const selected = scene.setSelection(own(refs), options?.add);
            options?.within?.();
            return selected;
        },
        clearSelection: () => scene.select(null),
        hide: (refs) => scene.setHiddenMany(own(refs), true),
        show: (refs) => scene.setHiddenMany(own(refs), false),
        isolate: (refs) => scene.isolate(own(refs)),
        objectVisibility: () => (model, id) =>
            model === scene.modelKey && scene.draws(id) ? (scene.isHidden(id) ? "hidden" : "shown") : null,
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
    };
}
