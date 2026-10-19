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
// frame's delta. There is no second rAF loop and no queue. While idle the
// step returns at its first line. Motion stops when the processed axes are
// all at rest, or when no motion report has arrived for STALE_MS -- not every
// device sends a final all-zero report when the cap is released.
//
// SMOOTHNESS. The axes pass an adaptive low-pass before the dead zone
// (./smoothing: hand tremor and sensor noise are what made a gentle glide
// jitter), and motion is scaled by an averaged frame time (DT_EMA); the gesture
// bracket outlives a stop by LINGER_MS, so brushing the dead zone does not
// restart it.
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
    FULL_SCALE,
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
import {createOneEuro, resetOneEuro, stepOneEuro} from "./smoothing";

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

/** Motion counts as stopped this long after the last motion report. */
const STALE_MS = 200;
/** The gesture (and the controls' start/end bracket) stays open this long
 *  after motion stops, so a glide that brushes the dead zone or skips a report
 *  is one gesture, not a start/end per hiccup. */
const LINGER_MS = 300;
/** Longest frame step applied: a stalled tab must not jump the camera. */
const MAX_DT = 0.1;
/** Weight of the newest frame in the frame-time average. The camera moves by
 *  rate x frame time; the raw rAF delta swings with every heavy frame, which
 *  turns into uneven speed even at a steady push. */
const DT_EMA = 0.2;

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
/** The raw axes after the adaptive low-pass (see ./smoothing), still in the
 *  device frame and units; the dead zone and the camera see only these. */
const filtered = new Float32Array(6);
const filter = createOneEuro(6);
const AT_REST = new Float32Array(6);
let dtAvg = 1 / 60;
let lastMovingAt = -Infinity;
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
    resetOneEuro(filter);
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
    const fresh = now - axis.lastMotionAt <= STALE_MS || now <= holdUntil;
    const frameDt = frameDelta < MAX_DT ? frameDelta : MAX_DT;
    // Without fresh reports the cap is taken as released: the filter eases
    // toward rest instead of the camera stopping dead.
    stepOneEuro(filter, fresh ? axis.raw : AT_REST, frameDt, FULL_SCALE, filtered);
    const moving = processAxes(filtered, settings, motion);
    if (moving) lastMovingAt = now;
    if (devStats) {
        if (wasFresh && !fresh && gestureControls) devStats.staleCuts++;
        wasFresh = fresh;
    }
    // With several viewers on the page, the device drives the one in use.
    if (getViewerRuntime().controls.current !== controls) return;

    if (!moving) {
        if (now - lastMovingAt > LINGER_MS) endGesture();
        return;
    }
    const t0 = devStats ? performance.now() : 0;
    const opening = gestureControls !== controls;
    // A gesture's first frame takes the real delta; after that, the average.
    dtAvg = opening ? frameDt : dtAvg + (frameDt - dtAvg) * DT_EMA;
    const dt = dtAvg;
    if (opening) {
        // Close a gesture still open on another viewer's controls.
        const prev = gestureControls;
        if (prev) (prev as any).dispatchEvent(prev instanceof OrbitControls ? ORBIT_END : CC_END);
        gestureControls = controls;
        (controls as any).dispatchEvent(controls instanceof OrbitControls ? ORBIT_START : CC_START);
        if (devStats) devStats.gestures++;
    }
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
        const fms = frameDelta * 1000;
        if (fms > devStats.maxFrameMs) devStats.maxFrameMs = fms;
        if (fms > 25) devStats.slowFrames++;
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
    const now = performance.now();
    const prevMotionAt = axis.lastMotionAt;
    const kind = applyInputReport(axis, e.reportId, e.data, now);
    if (devStats && kind === REPORT_MOTION && gestureControls) {
        const gap = now - prevMotionAt;
        if (gap > devStats.maxReportGapMs) devStats.maxReportGapMs = gap;
        devStats.reports++;
    }
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

// ---- the vendor driver's wheel events ---------------------------------------
//
// With the vendor's desktop driver running, the same cap motion we read over
// WebHID also reaches the browser as mouse-wheel (and Ctrl+wheel) events from
// the driver's profile for apps it does not know. The orbit controls take each
// one as a discrete dolly step, which shows as the camera jumping ahead in the
// middle of a smooth glide. While the cap is deflected, wheel events are
// therefore swallowed before they reach the canvas; a real wheel turn while
// holding the cap is not a thing anyone does.

/** Wheel events this soon after a motion report with the cap off rest are the driver's. */
const WHEEL_GUARD_MS = 150;

function capDeflected(): boolean {
    const r = axis.raw;
    for (let i = 0; i < 6; i++) if (r[i] !== 0) return true;
    return false;
}

function onWheel(event: WheelEvent): void {
    if (!device || !(event.target instanceof HTMLCanvasElement)) return;
    if (performance.now() - axis.lastMotionAt > WHEEL_GUARD_MS || !capDeflected()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (devStats) devStats.wheelsSwallowed++;
}

const WHEEL_OPTS: AddEventListenerOptions = {capture: true, passive: false};

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
    window.addEventListener("wheel", onWheel, WHEEL_OPTS);
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
        window.removeEventListener("wheel", onWheel, WHEEL_OPTS);
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
// With a real device it also says where stutter comes from: `gestures` (how
// often the start/end bracket reopened), `staleCuts` (motion stopped for lack
// of reports), `maxReportGapMs`/`reports` (the device's report cadence while
// moving), `maxFrameMs`/`slowFrames` (frames over 25 ms, i.e. render cost),
// and `wheelsSwallowed` (the vendor driver's wheel events dropped mid-glide).
// Not compiled into production builds.

interface DevStats {
    frames: number;
    ms: number;
    gestures: number;
    staleCuts: number;
    reports: number;
    maxReportGapMs: number;
    maxFrameMs: number;
    slowFrames: number;
    wheelsSwallowed: number;
}
let devStats: DevStats | null = null;
let wasFresh = false;
const emptyStats = (): DevStats => ({
    frames: 0, ms: 0, gestures: 0, staleCuts: 0, reports: 0, maxReportGapMs: 0, maxFrameMs: 0, slowFrames: 0,
    wheelsSwallowed: 0,
});

if (import.meta.env?.DEV && typeof window !== "undefined") {
    devStats = emptyStats();
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
            Object.assign(devStats!, emptyStats());
        },
    };
}
