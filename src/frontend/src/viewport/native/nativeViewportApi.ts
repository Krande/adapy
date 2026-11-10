// The three.js-free viewport behind the viewport interface (viewport/api/viewportApi.ts).
import {type CameraView} from "@/layout/coreCommandIds";
import {namedViewDirection} from "@/utils/camera/namedViewDirection";
import type {ViewportApi} from "@/viewport/api/viewportApi";
import type {NativeScene} from "./nativeScene";

/** One key press orbits by this much, and pans by this fraction of the view: as on the three.js viewport. */
const ORBIT_STEP_RAD = (15 * Math.PI) / 180;
const PAN_STEP_FRACTION = 0.1;
const MOVE_SECONDS = 0.25;

export function createNativeViewportApi(scene: NativeScene): ViewportApi {
    return {
        kind: "native",
        unavailable: () => null,
        selectionCount: () => scene.selectionSize,
        clearSelection: () => scene.select(null),
        modelBounds: () => scene.modelBounds,
        hideSelected: () => scene.hideSelected(),
        hideOthers: () => scene.hideOthers(),
        showAll: () => scene.showAll(),
        fitAll: () => scene.fit(MOVE_SECONDS),
        fitSelection: () => void scene.fitSelection(),
        setView: (view: CameraView) => scene.viewFrom(namedViewDirection(view)),
        resetCamera: () => scene.home(),
        orbitStep: (right, up) => scene.orbitBy(right * ORBIT_STEP_RAD, up * ORBIT_STEP_RAD),
        panStep: (right, up) => scene.panBy(right * PAN_STEP_FRACTION, up * PAN_STEP_FRACTION),
    };
}
