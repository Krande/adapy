// The browser half of the 3D-mouse input: WebHID device handling, and the
// per-frame step the viewer's render loop calls.
//
// LIFECYCLE follows the Options toggle (`useSpaceMouseStore.enabled`), through
// `bindSpaceMouseToOptions`, which the canvas calls on mount:
//   * off -- no `navigator.hid` listener, no device open, nothing asked of the
//     browser; the per-frame step is a single boolean test.
//   * on  -- reconnect silently to a device the user granted before
//     (`getDevices`), follow plug/unplug, and otherwise wait for the
//     "Connect SpaceMouse" button: the browser's device chooser needs a user
//     gesture, so it can only be opened from a click.
//
// LATENCY AND COST. An input report only folds its values into a preallocated
// array (no allocation, no React state) and marks a gesture active; the NEXT
// animation frame of the viewer's own render loop applies it, scaled by that
// frame's real delta. There is no second rAF loop and no queue. While idle the
// step returns at its first line. A gesture ends when the processed axes are
// all at rest, or when no motion report has arrived for STALE_MS -- not every
// device sends a final all-zero report when the cap is released.
//
// PEER OF THE MOUSE, NOT A RIVAL. The step moves the same camera position and
// orbit target the orbit controls own, before the loop calls their `update`,
// and brackets a gesture with the controls' own start/end events. Whatever the
// canvas does around a mouse orbit (adaptive pixel ratio, on-demand renders)
// therefore happens around a 3D-mouse gesture too, and nothing else does: no
// picking, no bounding boxes, no per-move refresh the mouse does not trigger.

import type CameraControls from "camera-controls";
import type * as THREE from "three";
import {Vector3} from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";

import {getViewerRuntime} from "@/state/viewerRuntime";
import {requestRender} from "@/state/perfStore";
import {type SpaceMouseButtonAction, useSpaceMouseStore} from "@/state/spaceMouseStore";
import {fitAllView, fitSelectionView} from "@/components/viewer/sceneHelpers/setupCameraControlsHandlers";
import {
    applyInputReport,
    createAxisState,
    HID_FILTERS,
    isSpaceMouse,
    MULTI_AXIS_USAGE,
    MULTI_AXIS_USAGE_PAGE,
    processAxes,
    REPORT_BUTTONS,
    REPORT_MOTION,
    resetAxes,
    takePressedButtons,
} from "./hidReport";
import {applyObjectModeMotion} from "./navigation";

// The slice of WebHID this file uses. TypeScript's DOM lib does not carry the
// API, and these few lines are cheaper than a types dependency.
interface HidCollection {
    usagePage: number;
    usage: number;
}
interface HidDevice extends EventTarget {
    readonly opened: boolean;
    readonly vendorId: number;
    readonly productId: number;
    readonly productName: string;
    readonly collections: HidCollection[];
    open(): Promise<void>;
    close(): Promise<void>;
}
interface HidInputReportEvent extends Event {
    readonly device: HidDevice;
    readonly reportId: number;
    readonly data: DataView;
}
interface HidConnectionEvent extends Event {
    readonly device: HidDevice;
}
interface Hid extends EventTarget {
    getDevices(): Promise<HidDevice[]>;
    requestDevice(options: {filters: ReadonlyArray<{vendorId: number; productId?: number}>}): Promise<HidDevice[]>;
}

type Controls = CameraControls | OrbitControls;

/** A gesture ends this long after the last motion report. */
const STALE_MS = 100;
/** Longest frame step applied: a stalled tab must not jump the camera. */
const MAX_DT = 0.05;

function getHid(): Hid | null {
    if (typeof navigator === "undefined") return null;
    return ((navigator as unknown as {hid?: Hid}).hid) ?? null;
}

/** WebHID is Chromium-only (Chrome, Edge, Opera) and secure-context only. */
export function isWebHidSupported(): boolean {
    return getHid() !== null;
}

// ---- input state (hot path: mutated in place, never reallocated) -----------

const axis = createAxisState();
const motion = new Float32Array(6);
const _position = new Vector3();
const _target = new Vector3();
let active = false;
/** The controls a gesture's start event went to, so its end goes there too. */
let gestureControls: Controls | null = null;
/** Dev hook: synthetic input holds until this time. */
let holdUntil = -Infinity;

const ORBIT_START = {type: "start"};
const ORBIT_END = {type: "end"};
const CC_START = {type: "controlstart"};
const CC_END = {type: "controlend"};

function endGesture(): void {
    active = false;
    const c = gestureControls;
    if (!c) return;
    gestureControls = null;
    (c as any).dispatchEvent(c instanceof OrbitControls ? ORBIT_END : CC_END);
}

/**
 * One frame of 3D-mouse motion. Called by the canvas render loop with that
 * frame's delta, BEFORE it updates the orbit controls.
 */
export function tickSpaceMouse(frameDelta: number, camera: THREE.PerspectiveCamera, controls: Controls): void {
    if (!active) return;
    const now = performance.now();
    const settings = useSpaceMouseStore.getState();
    if ((now - axis.lastMotionAt > STALE_MS && now > holdUntil) || !processAxes(axis.raw, settings, motion)) {
        endGesture();
        return;
    }
    // With several viewers on the page, the device drives the one in use.
    if (getViewerRuntime().controls.current !== controls) return;

    const t0 = devStats ? performance.now() : 0;
    if (gestureControls !== controls) {
        endGesture();
        active = true;
        gestureControls = controls;
        (controls as any).dispatchEvent(controls instanceof OrbitControls ? ORBIT_START : CC_START);
    }
    const dt = frameDelta < MAX_DT ? frameDelta : MAX_DT;
    if (controls instanceof OrbitControls) {
        // Moved in place; the loop's `controls.update()` re-aims the camera
        // and emits the change event the on-demand renderer listens for.
        applyObjectModeMotion(camera.position, controls.target, camera.up, motion, dt, camera.fov, settings.sensitivity);
    } else {
        controls.getPosition(_position);
        controls.getTarget(_target);
        if (applyObjectModeMotion(_position, _target, camera.up, motion, dt, camera.fov, settings.sensitivity)) {
            void controls.setLookAt(_position.x, _position.y, _position.z, _target.x, _target.y, _target.z, false);
        }
    }
    requestRender();
    if (devStats) {
        devStats.frames++;
        devStats.ms += performance.now() - t0;
    }
}

function runButton(action: SpaceMouseButtonAction): void {
    if (action === "none") return;
    const rt = getViewerRuntime();
    const scene = rt.scene.current;
    const camera = rt.camera.current;
    const controls = rt.controls.current;
    if (!camera || !controls) return;
    endGesture();
    if (action === "fitAll") {
        if (scene) fitAllView(scene, camera, controls);
    } else {
        fitSelectionView(camera, controls);
    }
    requestRender();
}

function onInputReport(event: Event): void {
    const e = event as HidInputReportEvent;
    const kind = applyInputReport(axis, e.reportId, e.data, performance.now());
    if (kind === REPORT_MOTION) {
        active = true;
    } else if (kind === REPORT_BUTTONS) {
        const pressed = takePressedButtons(axis);
        if (pressed === 0) return;
        const s = useSpaceMouseStore.getState();
        if (pressed & 1) runButton(s.button1);
        if (pressed & 2) runButton(s.button2);
    }
}

// ---- device handling ------------------------------------------------------

let device: HidDevice | null = null;
let listening = false;
/** Bumped by every enable/disable, so an `open()` that resolves after the
 *  toggle went off closes the device instead of adopting it. */
let generation = 0;

function setConnection(...args: Parameters<ReturnType<typeof useSpaceMouseStore.getState>["setConnection"]>): void {
    useSpaceMouseStore.getState().setConnection(...args);
}

function deviceLabel(d: HidDevice): string {
    return d.productName || "3D mouse";
}

/** The granted device to use: a 3D mouse, preferably the HID interface that
 *  carries the multi-axis collection when the browser lists several. */
function pickDevice(devices: readonly HidDevice[]): HidDevice | null {
    let fallback: HidDevice | null = null;
    for (const d of devices) {
        if (!isSpaceMouse(d.vendorId, d.productId)) continue;
        if (d.collections?.some((c) => c.usagePage === MULTI_AXIS_USAGE_PAGE && c.usage === MULTI_AXIS_USAGE)) return d;
        fallback ??= d;
    }
    return fallback;
}

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

async function openDevice(d: HidDevice, gen: number): Promise<void> {
    setConnection("connecting", deviceLabel(d));
    try {
        if (!d.opened) await d.open();
    } catch (err) {
        if (gen !== generation) return;
        setConnection(
            "error",
            deviceLabel(d),
            `Could not open ${deviceLabel(d)} (${errorText(err)}). Another program may hold it: ` +
                "on Windows, quit the 3DxWare driver (3DxService) and connect again.",
        );
        return;
    }
    if (gen !== generation) {
        void d.close().catch(() => undefined);
        return;
    }
    if (device && device !== d) releaseDevice();
    device = d;
    d.addEventListener("inputreport", onInputReport);
    setConnection("connected", deviceLabel(d));
}

function releaseDevice(): void {
    endGesture();
    resetAxes(axis);
    const d = device;
    device = null;
    if (!d) return;
    d.removeEventListener("inputreport", onInputReport);
    void d.close().catch(() => undefined);
}

function onConnect(event: Event): void {
    const d = (event as HidConnectionEvent).device;
    if (device || !isSpaceMouse(d.vendorId, d.productId)) return;
    void openDevice(d, generation);
}

function onDisconnect(event: Event): void {
    if ((event as HidConnectionEvent).device !== device) return;
    // Already gone, so nothing to close; just stop listening and stop moving.
    device?.removeEventListener("inputreport", onInputReport);
    device = null;
    endGesture();
    resetAxes(axis);
    setConnection("disconnected");
}

async function enable(): Promise<void> {
    const hid = getHid();
    if (!hid) {
        setConnection("unsupported");
        return;
    }
    if (listening) return;
    listening = true;
    const gen = ++generation;
    hid.addEventListener("connect", onConnect);
    hid.addEventListener("disconnect", onDisconnect);
    setConnection("disconnected");
    try {
        const d = pickDevice(await hid.getDevices());
        if (d && gen === generation) await openDevice(d, gen);
    } catch (err) {
        if (gen === generation) setConnection("error", null, errorText(err));
    }
}

function disable(): void {
    generation++;
    const hid = getHid();
    if (listening && hid) {
        hid.removeEventListener("connect", onConnect);
        hid.removeEventListener("disconnect", onDisconnect);
    }
    listening = false;
    releaseDevice();
    setConnection("off");
}

/**
 * Open the browser's device chooser and connect to the pick. Must be called
 * from a user gesture (a click); a cancelled chooser changes nothing.
 */
export async function requestSpaceMouse(): Promise<void> {
    const hid = getHid();
    if (!hid || !listening) return;
    const gen = generation;
    let picked: HidDevice[];
    try {
        picked = await hid.requestDevice({filters: HID_FILTERS});
    } catch (err) {
        // A permissions policy (an embedding iframe without `allow="hid"`)
        // lands here, as does a call outside a user gesture.
        if (gen === generation) setConnection("error", null, errorText(err));
        return;
    }
    const d = pickDevice(picked);
    if (!d || gen !== generation) return;
    await openDevice(d, gen);
}

let bindCount = 0;
let unsubscribe: (() => void) | null = null;

/**
 * Make the device follow the Options toggle for as long as a viewer canvas is
 * mounted. Reference-counted, so two canvases share one device; returns the
 * unbind.
 */
export function bindSpaceMouseToOptions(): () => void {
    if (bindCount++ === 0) {
        const sync = (on: boolean) => (on ? void enable() : disable());
        sync(useSpaceMouseStore.getState().enabled);
        unsubscribe = useSpaceMouseStore.subscribe((s, prev) => {
            if (s.enabled !== prev.enabled) sync(s.enabled);
        });
    }
    let bound = true;
    return () => {
        if (!bound) return;
        bound = false;
        if (--bindCount > 0) return;
        unsubscribe?.();
        unsubscribe = null;
        disable();
    };
}

// ---- dev-only measurement hook --------------------------------------------
//
// `__adaSpaceMouse.feed([tx, ty, tz, rx, ry, rz], ms)` (device-frame units,
// +-350 = full deflection) drives the camera as if the cap were held for `ms`,
// with no device; `stats()` reports how many frames the step applied and the
// time spent in it. Lets the per-frame cost be measured on a loaded scene.
// Not compiled into production builds.

let devStats: {frames: number; ms: number} | null = null;

if (import.meta.env?.DEV && typeof window !== "undefined") {
    devStats = {frames: 0, ms: 0};
    (window as any).__adaSpaceMouse = {
        feed(values: number[], ms = 1000) {
            for (let i = 0; i < 6; i++) axis.raw[i] = values[i] ?? 0;
            const now = performance.now();
            axis.lastMotionAt = now;
            holdUntil = now + ms;
            active = true;
        },
        stop() {
            holdUntil = -Infinity;
            axis.raw.fill(0);
        },
        stats() {
            return {...devStats!, msPerFrame: devStats!.frames ? devStats!.ms / devStats!.frames : 0};
        },
        resetStats() {
            devStats!.frames = 0;
            devStats!.ms = 0;
        },
    };
}
