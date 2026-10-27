// The pure half of the 3D-mouse input: which HID devices count as one, what
// their input reports say, and how six raw axis readings become the six
// unitless motion values the camera integration consumes.
//
// Nothing here touches the browser, so all of it runs under `node --test`. The
// WebHID adapter (`spaceMouseDevice.ts`) feeds reports in and reads values out.
//
// WHY READ THE HID REPORTS AT ALL. The vendor's own web path is a script that
// talks to its desktop driver over a local websocket: it needs that driver
// installed and running, and it ships under the vendor's SDK licence rather than
// one this repository can carry. The device itself is a plain HID multi-axis
// controller, and WebHID (Chromium-based browsers) reads it with no driver, no
// network and no licence question.
//
// THE REPORTS. Every device in the family sends signed 16-bit little-endian
// axis values and a button bitmask:
//   * report 1 -- translation x, y, z (6 bytes); newer devices put all six axes
//     in this one report (12 bytes: tx, ty, tz, rx, ry, rz)
//   * report 2 -- rotation x, y, z (6 bytes), on the devices that split them
//   * report 3 -- buttons, one bit per button, bit 0 = button 1
// WebHID hands over `reportId` separately and `data` WITHOUT the id byte.
//
// THE DEVICE FRAME is not the view frame. The HID axes are x right, y toward
// the user, z down (pushing the cap down reads +z). The camera code works in the
// usual view frame -- x right, y up, z toward the viewer -- which is the device
// frame turned 90 degrees about x: view = (x, -z, y), for translation and
// rotation alike (it is a proper rotation, so axial and polar vectors map the
// same way). Per-axis invert settings cover a device that disagrees.
//
// HOT PATH. Reports arrive at up to a few hundred per second, and the processed
// values are read once per animation frame, so every function here writes into
// a caller-owned typed array and allocates nothing.

/** The current vendor id, and the one its older devices were sold under. */
export const VENDOR_ID = 0x256f;
export const LEGACY_VENDOR_ID = 0x046d;

/** The older vendor id is shared with a whole range of unrelated mice and
 *  keyboards, so under it only these products are 3D mice: SpaceMouse Plus XT,
 *  CadMan, SpaceMouse Classic, SpaceBall 5000, SpaceTraveler, SpacePilot,
 *  SpaceNavigator, SpaceExplorer, SpaceNavigator for Notebooks, SpacePilot Pro
 *  and SpaceMouse Pro. Everything under the current vendor id is one. */
export const LEGACY_PRODUCT_IDS: readonly number[] = [
    0xc603, 0xc605, 0xc606, 0xc621, 0xc623, 0xc625, 0xc626, 0xc627, 0xc628, 0xc629, 0xc62b,
];

/** The filter list for `navigator.hid.requestDevice`, so the browser's chooser
 *  only offers 3D mice. */
export const HID_FILTERS: ReadonlyArray<{vendorId: number; productId?: number}> = [
    {vendorId: VENDOR_ID},
    ...LEGACY_PRODUCT_IDS.map((productId) => ({vendorId: LEGACY_VENDOR_ID, productId})),
];

/** HID usage of the collection that carries the motion reports: Generic Desktop
 *  / Multi-axis Controller. A device can expose other collections beside it. */
export const MULTI_AXIS_USAGE_PAGE = 0x01;
export const MULTI_AXIS_USAGE = 0x08;

export function isSpaceMouse(vendorId: number, productId: number): boolean {
    if (vendorId === VENDOR_ID) return true;
    return vendorId === LEGACY_VENDOR_ID && LEGACY_PRODUCT_IDS.includes(productId);
}

/** What the devices report at full deflection, roughly; the newer ones reach a
 *  little further, which the clamp in `processAxes` absorbs. */
export const FULL_SCALE = 350;

/** Axis order everywhere in this module and its callers. */
export const AXES = ["tx", "ty", "tz", "rx", "ry", "rz"] as const;
export type AxisName = (typeof AXES)[number];

/** Live device state. `raw` is in the DEVICE frame, in device units. */
export interface AxisState {
    raw: Float32Array;
    buttons: number;
    prevButtons: number;
    /** `performance.now()` of the last motion report, for the stale cut-off. */
    lastMotionAt: number;
}

export function createAxisState(): AxisState {
    return {raw: new Float32Array(6), buttons: 0, prevButtons: 0, lastMotionAt: -Infinity};
}

export const REPORT_NONE = 0;
export const REPORT_MOTION = 1;
export const REPORT_BUTTONS = 2;
export type ReportKind = typeof REPORT_NONE | typeof REPORT_MOTION | typeof REPORT_BUTTONS;

/** Fold one input report into `state`. Returns what kind of report it was. */
export function applyInputReport(state: AxisState, reportId: number, data: DataView, now: number): ReportKind {
    const n = data.byteLength;
    if (reportId === 1) {
        if (n < 6) return REPORT_NONE;
        const raw = state.raw;
        raw[0] = data.getInt16(0, true);
        raw[1] = data.getInt16(2, true);
        raw[2] = data.getInt16(4, true);
        if (n >= 12) {
            raw[3] = data.getInt16(6, true);
            raw[4] = data.getInt16(8, true);
            raw[5] = data.getInt16(10, true);
        }
        state.lastMotionAt = now;
        return REPORT_MOTION;
    }
    if (reportId === 2) {
        if (n < 6) return REPORT_NONE;
        const raw = state.raw;
        raw[3] = data.getInt16(0, true);
        raw[4] = data.getInt16(2, true);
        raw[5] = data.getInt16(4, true);
        state.lastMotionAt = now;
        return REPORT_MOTION;
    }
    if (reportId === 3) {
        let bits = 0;
        const bytes = n < 4 ? n : 4;
        for (let i = 0; i < bytes; i++) bits |= data.getUint8(i) << (8 * i);
        state.buttons = bits >>> 0;
        return REPORT_BUTTONS;
    }
    return REPORT_NONE;
}

/** Buttons that went down since the last call, as a bitmask; consumes the edge. */
export function takePressedButtons(state: AxisState): number {
    const pressed = (state.buttons & ~state.prevButtons) >>> 0;
    state.prevButtons = state.buttons;
    return pressed;
}

/** Forget all motion, e.g. on disconnect: a device unplugged mid-push must not
 *  keep the camera moving. */
export function resetAxes(state: AxisState): void {
    state.raw.fill(0);
    state.buttons = 0;
    state.prevButtons = 0;
    state.lastMotionAt = -Infinity;
}

/** The axis-shaping settings, a subset of the persisted SpaceMouse options. */
export interface AxisSettings {
    /** Fraction of full scale ignored around rest, 0..<1. */
    deadZone: number;
    /** Per axis, in `AXES` order: flip its sign. */
    invert: readonly boolean[];
    /** Keep only the axis pushed hardest this frame. */
    dominantAxis: boolean;
    /** Drop the rotation axes (pan and zoom only). */
    lockRotation: boolean;
    /** Drop the translation axes (rotate only). */
    lockPan: boolean;
    /** Keep the roll axis. Off, it is dropped like a locked one, so it can neither
     *  move the view nor win the dominant-axis pick. */
    enableRoll?: boolean;
}

/**
 * Turn the device-frame `raw` readings into view-frame motion in [-1, 1],
 * written to `out` (length 6, `AXES` order). Returns false when every axis is
 * at rest after the dead zone and locks, so the caller can stop.
 *
 * The dead zone is rescaled rather than subtracted: the response starts at 0 at
 * its edge and still reaches 1 at full deflection, so there is no jump when a
 * push leaves it and no top speed lost to it.
 */
export function processAxes(raw: ArrayLike<number>, settings: AxisSettings, out: Float32Array): boolean {
    // device (x right, y toward user, z down) -> view (x right, y up, z toward viewer)
    out[0] = raw[0];
    out[1] = -raw[2];
    out[2] = raw[1];
    out[3] = raw[3];
    out[4] = -raw[5];
    out[5] = raw[4];

    const dz = settings.deadZone > 0 ? (settings.deadZone < 0.95 ? settings.deadZone : 0.95) : 0;
    const invert = settings.invert;
    let strongest = -1;
    let strongestMag = 0;
    for (let i = 0; i < 6; i++) {
        let v = out[i] / FULL_SCALE;
        if (v > 1) v = 1;
        else if (v < -1) v = -1;
        const mag = v < 0 ? -v : v;
        if (mag <= dz) v = 0;
        else v = (v < 0 ? -1 : 1) * (mag - dz) / (1 - dz);
        if (invert[i]) v = -v;
        if ((i < 3 && settings.lockPan) || (i >= 3 && settings.lockRotation)) v = 0;
        if (i === 5 && !settings.enableRoll) v = 0;
        out[i] = v;
        const m = v < 0 ? -v : v;
        if (m > strongestMag) {
            strongestMag = m;
            strongest = i;
        }
    }
    if (strongest < 0) return false;
    if (settings.dominantAxis) {
        for (let i = 0; i < 6; i++) if (i !== strongest) out[i] = 0;
    }
    return true;
}
