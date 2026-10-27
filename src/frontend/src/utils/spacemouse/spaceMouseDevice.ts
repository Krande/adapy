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
import {applyObjectModeMotion, DEFAULT_RATES, rolledUp} from "./navigation";
import {createOneEuro, resetOneEuro, stepOneEuro} from "./smoothing";
import type {InputWindow} from "@/utils/scene/renderInputSummary";

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

/** 3Dconnexion wireless receivers: the SpaceMouse Wireless one and the Universal Receiver. */
const RECEIVER_PRODUCT_IDS: readonly number[] = [0xc62f, 0xc652];

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
const _up = new Vector3();
let active = false;

// ---- roll (opt-in, `enableRoll`) ---------------------------------------------
//
// The orbit controls keep the camera level: their update re-aims it with
// `lookAt` against `camera.up`. Roll is therefore kept as an angle about the view
// direction, and `camera.up` is re-derived from it every frame (`rolledUp`) --
// after a mouse orbit too, so the roll stays a roll of the VIEW rather than a
// tilted up vector the next orbit would smear across the model. Zero roll is the
// camera's own up, recorded the first time a camera is seen, which is also what
// switching roll off goes back to.

/** The view's roll about its direction, radians. */
let rollAngle = 0;
/** Each camera's level up vector, before any roll. */
const levelUps = new WeakMap<THREE.Camera, Vector3>();

function levelUpOf(camera: THREE.Camera): Vector3 {
    let up = levelUps.get(camera);
    if (!up) {
        up = camera.up.clone();
        levelUps.set(camera, up);
    }
    return up;
}

function poseOf(camera: THREE.Camera, controls: Controls): void {
    if (controls instanceof OrbitControls) {
        _position.copy(camera.position);
        _target.copy(controls.target);
    } else {
        controls.getPosition(_position);
        controls.getTarget(_target);
    }
}

/** Put `camera.up` where the roll says and re-aim; true when it moved. */
function applyRoll(camera: THREE.Camera, controls: Controls, levelUp: Vector3): boolean {
    poseOf(camera, controls);
    rolledUp(_position, _target, levelUp, rollAngle, _up);
    if (_up.distanceToSquared(camera.up) < 1e-14) return false;
    camera.up.copy(_up);
    camera.lookAt(_target);
    return true;
}

/** Level the view again: what switching roll off does. */
function resetRoll(): void {
    rollAngle = 0;
    const rt = getViewerRuntime();
    const camera = rt.camera.current;
    const controls = rt.controls.current;
    const level = camera ? levelUps.get(camera) : undefined;
    if (!camera || !controls || !level) return;
    if (applyRoll(camera, controls, level)) requestRender();
}
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
    const levelUp = levelUpOf(camera);
    // A rolled view stays rolled whatever moved it (a mouse orbit, a fit), before the
    // controls' update re-aims the camera against `camera.up`.
    if (rollAngle !== 0 && getViewerRuntime().controls.current === controls) applyRoll(camera, controls, levelUp);
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
    if (devStats || probe) {
        if (wasFresh && !fresh && gestureControls) {
            if (devStats) devStats.staleCuts++;
            if (probe) probe.staleCuts++;
        }
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
        if (probe) probe.gestures++;
    }
    // Turns and tilts about the level up, not the rolled one: the turntable stays
    // the turntable the mouse orbits with.
    if (controls instanceof OrbitControls) {
        // Moved in place; the loop's `controls.update()` re-aims the camera
        // and emits the change event the on-demand renderer listens for.
        applyObjectModeMotion(
            camera.position, controls.target, levelUp, motion, dt, camera.fov, settings.sensitivity, DEFAULT_RATES, rollAngle,
        );
    } else {
        controls.getPosition(_position);
        controls.getTarget(_target);
        if (applyObjectModeMotion(
            _position, _target, levelUp, motion, dt, camera.fov, settings.sensitivity, DEFAULT_RATES, rollAngle,
        )) {
            void controls.setLookAt(_position.x, _position.y, _position.z, _target.x, _target.y, _target.z, false);
        }
    }
    if (motion[5] !== 0) {
        // processAxes drops roll unless it is enabled, so this only runs when it is.
        rollAngle += motion[5] * DEFAULT_RATES.rotate * settings.sensitivity * dt;
        if (rollAngle > Math.PI) rollAngle -= 2 * Math.PI;
        else if (rollAngle < -Math.PI) rollAngle += 2 * Math.PI;
    }
    // Every moving frame: a turn or tilt changes the view direction the roll is about.
    if (rollAngle !== 0 || motion[5] !== 0) applyRoll(camera, controls, levelUp);
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

/** The browser delivers HID reports whatever window is in front; the device
 *  drives this page only while it has focus, like the mouse and keyboard. */
function pageFocused(): boolean {
    return typeof document === "undefined" || document.hasFocus();
}

function onInputReport(event: Event): void {
    if (!pageFocused()) return;
    const e = event as HidInputReportEvent;
    const now = performance.now();
    const prevMotionAt = axis.lastMotionAt;
    const kind = applyInputReport(axis, e.reportId, e.data, now);
    if (probe) probe.hidReports++;
    if (devStats && kind === REPORT_MOTION && gestureControls) {
        const gap = now - prevMotionAt;
        if (gap > devStats.maxReportGapMs) devStats.maxReportGapMs = gap;
        devStats.reports++;
    }
    if (probe && kind === REPORT_MOTION) recordReport(probe, prevMotionAt, now);
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
    if (!(event.target instanceof HTMLCanvasElement)) return;
    if (probe) probe.wheels++;
    if (!device) return;
    if (performance.now() - axis.lastMotionAt > WHEEL_GUARD_MS || !capDeflected()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (devStats) devStats.wheelsSwallowed++;
}

const WHEEL_OPTS: AddEventListenerOptions = {capture: true, passive: false};

/** Focus left the page mid-glide: stop, rather than keep the last deflection. */
function onBlur(): void {
    endGesture();
    resetAxes(axis);
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

/** A wireless receiver rather than the device on its cable. A receiver's
 *  reports come over the radio link, sparser and burstier than over the cable. */
function isReceiver(d: HidDevice): boolean {
    return RECEIVER_PRODUCT_IDS.includes(d.productId) || /receiver/i.test(d.productName ?? "");
}

/** How much a granted device is preferred: a cabled device over a receiver,
 *  and the HID interface carrying the multi-axis collection over another. */
function deviceRank(d: HidDevice): number {
    const multiAxis = d.collections?.some((c) => c.usagePage === MULTI_AXIS_USAGE_PAGE && c.usage === MULTI_AXIS_USAGE);
    return (isReceiver(d) ? 0 : 2) + (multiAxis ? 1 : 0);
}

/** The granted 3D mouse to use, the best ranked when the browser lists several. */
function pickDevice(devices: readonly HidDevice[]): HidDevice | null {
    let best: HidDevice | null = null;
    for (const d of devices) {
        if (!isSpaceMouse(d.vendorId, d.productId)) continue;
        if (!best || deviceRank(d) > deviceRank(best)) best = d;
    }
    return best;
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
    if (!isSpaceMouse(d.vendorId, d.productId)) return;
    // A cable plugged in while the receiver is in use takes over from it.
    if (device && deviceRank(d) <= deviceRank(device)) return;
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
    window.addEventListener("blur", onBlur);
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
        window.removeEventListener("blur", onBlur);
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
            // Off levels the view: a camera left at an angle with nothing to undo it.
            if ((!s.enableRoll && prev.enableRoll) || (!s.enabled && prev.enabled)) resetRoll();
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

// ---- render-profile probe ---------------------------------------------------
//
// While the admin render profiler runs, each of its windows also records the
// device's report cadence, so input stutter lands in the same audit row as the
// frame times: gaps between motion reports while the cap is held, and the
// spans of the slow ones, which the profiler matches against main-thread long
// tasks (a busy main thread delays every report queued behind it).

/** A gap longer than this, between reports of one hold, is the device going quiet. */
const HOLD_GAP_MS = 1000;
/** Gaps longer than this are kept as spans, to match against long tasks. */
const SLOW_GAP_MS = 50;
const MAX_SLOW_GAPS = 200;

interface Probe {
    gaps: number[];
    slowGaps: Array<[number, number]>;
    gestures: number;
    staleCuts: number;
    /** Every input report, of any kind; motion ones are in `gaps`. */
    hidReports: number;
    /** Wheel events on a canvas, swallowed or not: the vendor driver's show here. */
    wheels: number;
}
let probe: Probe | null = null;

function recordReport(p: Probe, prevAt: number, now: number): void {
    const gap = now - prevAt;
    if (!(gap <= HOLD_GAP_MS)) return;
    p.gaps.push(gap);
    if (gap > SLOW_GAP_MS && p.slowGaps.length < MAX_SLOW_GAPS) p.slowGaps.push([prevAt, now]);
}

/** Start (or restart) collecting a window. */
export function startSpaceMouseProbe(): void {
    probe = {gaps: [], slowGaps: [], gestures: 0, staleCuts: 0, hidReports: 0, wheels: 0};
}

/** Stop collecting; returns the window, or null while the 3D mouse option is off. */
export function takeSpaceMouseProbe(): InputWindow | null {
    const p = probe;
    probe = null;
    if (!p || !listening) return null;
    return {
        connection: useSpaceMouseStore.getState().connection,
        device: device ? deviceLabel(device) : null,
        product_id: device ? device.productId.toString(16).padStart(4, "0") : null,
        hidReports: p.hidReports,
        wheels: p.wheels,
        gaps: p.gaps,
        slowGaps: p.slowGaps,
        gestures: p.gestures,
        staleCuts: p.staleCuts,
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
        /** This module's own view, to compare with what Options shows. */
        state() {
            return {
                listening,
                bindCount,
                device: device ? {name: deviceLabel(device), productId: device.productId.toString(16), opened: device.opened} : null,
                connection: useSpaceMouseStore.getState().connection,
                enabled: useSpaceMouseStore.getState().enabled,
                probing: probe !== null,
                active,
            };
        },
    };
}
