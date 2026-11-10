// Applies the camera input mapping (state/cameraControlsStore) to the three.js camera controllers, and
// keeps them in step when it changes. Both controllers read the mouse themselves, so the mapping is
// translated into their own settings; the modifier a drag is held with is applied just before the
// controller sees the press (a capturing listener on the same input surface).
import {MOUSE, TOUCH} from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import CameraControls from "camera-controls";
import {useCameraControlsStore} from "@/state/cameraControlsStore";
import {dragAction, mouseButtonOf, type CameraAction, type CameraMapping, type MouseButton} from "@/utils/camera/cameraMapping";

// The controllers' own speeds and smoothing as they ship, which the mapping's multipliers scale.
const ORBIT_ROTATE_SPEED = 1;
const ORBIT_ZOOM_SPEED = 1;
const ORBIT_DAMPING_FACTOR = 0.1;
const CAMERA_ROTATE_SPEED = 1;
const CAMERA_DOLLY_SPEED = 1;
const CAMERA_SMOOTH_TIME = 0.25;
const CAMERA_DRAGGING_SMOOTH_TIME = 0.125;

const ORBIT_BUTTON = {left: "LEFT", middle: "MIDDLE", right: "RIGHT"} as const;

/** OrbitControls swaps rotate and pan itself while Shift, Ctrl or Cmd is held. Given the action wanted
 *  and whether it will swap, the button setting that produces it. -1 = the button does nothing. */
export function orbitControlsButton(action: CameraAction, willSwap: boolean): MOUSE | -1 {
    switch (action) {
        case "orbit": return willSwap ? MOUSE.PAN : MOUSE.ROTATE;
        case "pan": return willSwap ? MOUSE.ROTATE : MOUSE.PAN;
        case "zoom": return MOUSE.DOLLY;
        default: return -1;
    }
}

function applyToOrbitControls(controls: OrbitControls, m: CameraMapping): void {
    for (const button of ["left", "middle", "right"] as const) {
        controls.mouseButtons[ORBIT_BUTTON[button]] = orbitControlsButton(m.drag[button].none, false) as MOUSE;
    }
    controls.touches.ONE = m.touch.one === "pan" ? TOUCH.PAN : TOUCH.ROTATE;
    controls.touches.TWO = m.touch.two === "zoom-orbit" ? TOUCH.DOLLY_ROTATE : TOUCH.DOLLY_PAN;
    controls.enableRotate = true;
    controls.enableZoom = m.wheel.zoom;
    controls.zoomToCursor = m.wheel.toCursor;
    controls.rotateSpeed = ORBIT_ROTATE_SPEED * m.orbitSpeed;
    // A negative speed turns the wheel round: OrbitControls has no setting of its own for that.
    controls.zoomSpeed = ORBIT_ZOOM_SPEED * m.zoomSpeed * (m.wheel.inverted ? -1 : 1);
    controls.enableDamping = m.damping;
    controls.dampingFactor = ORBIT_DAMPING_FACTOR;
}

function cameraControlsAction(action: CameraAction): number {
    switch (action) {
        case "orbit": return CameraControls.ACTION.ROTATE;
        case "pan": return CameraControls.ACTION.TRUCK;
        case "zoom": return CameraControls.ACTION.DOLLY;
        default: return CameraControls.ACTION.NONE;
    }
}

function applyToCameraControls(controls: CameraControls, m: CameraMapping): void {
    for (const button of ["left", "middle", "right"] as const) {
        controls.mouseButtons[button] = cameraControlsAction(m.drag[button].none) as typeof controls.mouseButtons.left;
    }
    controls.mouseButtons.wheel = (m.wheel.zoom ? CameraControls.ACTION.DOLLY : CameraControls.ACTION.NONE) as typeof controls.mouseButtons.wheel;
    controls.touches.one = (m.touch.one === "pan" ? CameraControls.ACTION.TOUCH_TRUCK : m.touch.one === "none" ? CameraControls.ACTION.NONE : CameraControls.ACTION.TOUCH_ROTATE) as typeof controls.touches.one;
    controls.touches.two = (m.touch.two === "zoom-orbit" ? CameraControls.ACTION.TOUCH_DOLLY_ROTATE : m.touch.two === "none" ? CameraControls.ACTION.NONE : CameraControls.ACTION.TOUCH_DOLLY_TRUCK) as typeof controls.touches.two;
    controls.dollyToCursor = m.wheel.toCursor;
    controls.azimuthRotateSpeed = CAMERA_ROTATE_SPEED * m.orbitSpeed;
    controls.polarRotateSpeed = CAMERA_ROTATE_SPEED * m.orbitSpeed;
    controls.dollySpeed = CAMERA_DOLLY_SPEED * m.zoomSpeed * (m.wheel.inverted ? -1 : 1);
    controls.smoothTime = m.damping ? CAMERA_SMOOTH_TIME : 0;
    controls.draggingSmoothTime = m.damping ? CAMERA_DRAGGING_SMOOTH_TIME : 0;
}

/** Set the pressed button's action for the modifiers held, before the controller handles the press. */
function applyPress(controls: CameraControls | OrbitControls, m: CameraMapping, button: MouseButton, e: PointerEvent): void {
    const action = dragAction(m, button, e);
    if (controls instanceof OrbitControls) {
        const willSwap = e.shiftKey || e.ctrlKey || e.metaKey;
        controls.mouseButtons[ORBIT_BUTTON[button]] = orbitControlsButton(action, willSwap) as MOUSE;
    } else {
        controls.mouseButtons[button] = cameraControlsAction(action) as typeof controls.mouseButtons.left;
    }
}

/** Keep `controls` configured from the mapping store. Returns the function that stops doing so. */
export function bindCameraMapping(controls: CameraControls | OrbitControls, input: HTMLElement): () => void {
    const apply = (m: CameraMapping) => (controls instanceof OrbitControls ? applyToOrbitControls(controls, m) : applyToCameraControls(controls, m));
    apply(useCameraControlsStore.getState().mapping);
    const unsubscribe = useCameraControlsStore.subscribe((s, prev) => {
        if (s.mapping !== prev.mapping) apply(s.mapping);
    });
    const onPointerDown = (e: PointerEvent) => {
        const button = e.pointerType === "mouse" ? mouseButtonOf(e.button) : null;
        if (button) applyPress(controls, useCameraControlsStore.getState().mapping, button, e);
    };
    input.addEventListener("pointerdown", onPointerDown, {capture: true});
    return () => {
        unsubscribe();
        input.removeEventListener("pointerdown", onPointerDown, {capture: true});
    };
}
