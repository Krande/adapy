// The three.js-free viewport behind the viewport interface (viewport/api/viewportApi.ts).
import {type CameraView} from "@/layout/coreCommandIds";
import {namedViewDirection} from "@/utils/camera/namedViewDirection";
import type {ViewportApi} from "@/viewport/api/viewportApi";
import type {NativeScene} from "./nativeScene";

/** One key press orbits by this much, and pans by this fraction of the view: as on the three.js viewport. */
const ORBIT_STEP_RAD = (15 * Math.PI) / 180;
const PAN_STEP_FRACTION = 0.1;

export function createNativeViewportApi(scene: NativeScene): ViewportApi {
    return {
        kind: "native",
        // The renderer does not report an object's own bounds yet, so there is nothing to frame it by.
        unavailable: (feature) => (feature === "fit-selection" ? "Not available on the three.js-free viewport yet" : null),
        selectionCount: () => scene.selectionSize,
        clearSelection: () => scene.select(null),
        hideSelected: () => scene.hideSelected(),
        hideOthers: () => scene.hideOthers(),
        showAll: () => scene.showAll(),
        fitAll: () => scene.fit(),
        fitSelection: () => {},
        setView: (view: CameraView) => scene.viewFrom(namedViewDirection(view)),
        resetCamera: () => scene.fit(),
        orbitStep: (right, up) => scene.orbitBy(right * ORBIT_STEP_RAD, up * ORBIT_STEP_RAD),
        panStep: (right, up) => scene.panBy(right * PAN_STEP_FRACTION, up * PAN_STEP_FRACTION),
    };
}
