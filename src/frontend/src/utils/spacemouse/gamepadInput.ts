// The pure half of the 3D mouse over the Gamepad API.
//
// WHY A SECOND TRANSPORT. WebHID needs the browser's device chooser the first
// time (a click and a permission), and it cannot open a device another program
// already holds -- on Windows that is the vendor's driver service, whenever it
// runs. The Gamepad API needs no permission and no exclusive access: Chromium
// browsers list a 3D mouse as a joystick-class device, with its six axes as
// `Gamepad.axes`, once it has been moved on the page. That is all this file
// relies on; it reads the browser's own standard API and nothing from the vendor.
//
// WHAT IS NOT VERIFIED. The axis order, the signs and the button numbering below
// are an assumption -- the browser lists the device's HID axes in usage order, so
// the default is "the same as the HID path" -- and have not been checked on
// hardware. They live in ONE table (`PAD_MAPS`) so a device that disagrees is a
// one-line fix, and the Invert options in Options are the user's escape hatch
// meanwhile. Some devices report their axes as relative values, which browsers
// cannot always expose; those show up as a pad with fewer than six axes (see
// `selectPad`'s "noAxes").
//
// Nothing here touches the browser: the pad objects come in as plain data.

import {
    type AxisState,
    FULL_SCALE,
    isSpaceMouse,
    LEGACY_VENDOR_ID,
} from "./hidReport";

/** The slice of a `Gamepad` this module reads. */
export interface PadLike {
    readonly id: string;
    readonly connected?: boolean;
    readonly axes: ArrayLike<number>;
    readonly buttons: ArrayLike<{readonly pressed: boolean}>;
}

export interface PadId {
    vendorId: number;
    productId: number;
    name: string;
}

/** The vendor driver's virtual joystick, which carries the cap's axes in
 *  browsers that do not list the device itself (the vendor's own id pair). */
export const VIRTUAL_VENDOR_ID = 0xbeef;
export const VIRTUAL_PRODUCT_ID = LEGACY_VENDOR_ID;

/** Chromium: `Name (Vendor: 046d Product: c62b)`, or
 *  `Name (STANDARD GAMEPAD Vendor: 046d Product: c626)`. */
const CHROMIUM_ID = /^(.*?)\s*\((?:[^()]*?\s)?Vendor:\s*([0-9a-f]{1,4})\s+Product:\s*([0-9a-f]{1,4})\)\s*$/i;
/** Firefox: `046d-c62b-Name`, ids without leading zeros allowed. */
const FIREFOX_ID = /^([0-9a-f]{1,4})-([0-9a-f]{1,4})-(.*)$/i;

/** Vendor and product ids out of a `Gamepad.id`, in either browser's format; null
 *  when the string has neither (an Xbox pad's `Xbox 360 Controller (XInput ...)`). */
export function parsePadId(id: string): PadId | null {
    let m = CHROMIUM_ID.exec(id);
    if (m) return {vendorId: parseInt(m[2], 16), productId: parseInt(m[3], 16), name: m[1] || "3D mouse"};
    m = FIREFOX_ID.exec(id);
    if (m) return {vendorId: parseInt(m[1], 16), productId: parseInt(m[2], 16), name: m[3] || "3D mouse"};
    return null;
}

export type PadKind = "device" | "virtual";

export interface PadInfo extends PadId {
    kind: PadKind;
}

/** Whether a gamepad id is a 3D mouse: the device itself, or the driver's virtual joystick. */
export function classifyPad(id: string): PadInfo | null {
    const p = parsePadId(id);
    if (!p) return null;
    if (isSpaceMouse(p.vendorId, p.productId)) return {...p, kind: "device"};
    if (p.vendorId === VIRTUAL_VENDOR_ID && p.productId === VIRTUAL_PRODUCT_ID) return {...p, kind: "virtual"};
    return null;
}

/** A pad that can drive the camera has at least the six motion axes. */
export function padHasAxes(pad: PadLike): boolean {
    return pad.axes.length >= 6;
}

// ---- the per-device map -----------------------------------------------------

export interface PadMap {
    /** For device axis i (tx ty tz rx ry rz, the HID frame `processAxes` takes),
     *  the index of the `Gamepad.axes` entry that carries it. */
    order: readonly number[];
    /** For device axis i: +1, or -1 when the browser's sign is opposite the HID report's. */
    sign: readonly number[];
    /** For `Gamepad.buttons[i]`: the bit it sets in our button mask (bit 0 = button 1). -1 ignores it. */
    buttons: readonly number[];
}

export const DEFAULT_PAD_MAP: PadMap = {
    order: [0, 1, 2, 3, 4, 5],
    sign: [1, 1, 1, 1, 1, 1],
    buttons: Array.from({length: 32}, (_, i) => i),
};

/** Overrides by `vvvv:pppp` (lower-case hex). Empty on purpose: nothing has been checked
 *  on hardware yet, so every device starts from the default; add a row here when one does. */
export const PAD_MAPS: Readonly<Record<string, PadMap>> = {};

export function padKey(vendorId: number, productId: number): string {
    return `${vendorId.toString(16).padStart(4, "0")}:${productId.toString(16).padStart(4, "0")}`;
}

export function padMapFor(vendorId: number, productId: number): PadMap {
    return PAD_MAPS[padKey(vendorId, productId)] ?? DEFAULT_PAD_MAP;
}

// ---- choosing a pad ---------------------------------------------------------

export interface PadChoice {
    pad: PadLike;
    info: PadInfo;
    /** "active": six axes, usable. "noAxes": recognised, but the browser exposes fewer than six. */
    kind: "active" | "noAxes";
}

/** The 3D mouse to read among the pads the browser lists: a real device over the
 *  driver's virtual joystick (both are listed at once, and the joystick stays at
 *  rest), a usable pad over one without axes. A virtual joystick without six axes is
 *  just some other joystick, so it never counts as "found but no axes". */
export function selectPad(pads: ArrayLike<PadLike | null | undefined> | null | undefined): PadChoice | null {
    if (!pads) return null;
    let best: PadChoice | null = null;
    let bestRank = -1;
    for (let i = 0; i < pads.length; i++) {
        const pad = pads[i];
        if (!pad || pad.connected === false) continue;
        const info = classifyPad(pad.id);
        if (!info) continue;
        const usable = padHasAxes(pad);
        if (!usable && info.kind !== "device") continue;
        const rank = (usable ? 2 : 0) + (info.kind === "device" ? 1 : 0);
        if (rank > bestRank) {
            bestRank = rank;
            best = {pad, info, kind: usable ? "active" : "noAxes"};
        }
    }
    return best;
}

// ---- reading ----------------------------------------------------------------

/** Deflection (0..1) below which a reading is rest, for deciding whether the cap
 *  is moving at all. The camera's own dead zone comes later and is the user's. */
export const MOVE_THRESHOLD = 0.02;

/**
 * Write the pad's six axes into `state.raw` in the HID path's units (device frame,
 * +-FULL_SCALE), with `map` applied, and its buttons into `state.buttons`. Marks
 * `state.lastMotionAt` while any axis is past `MOVE_THRESHOLD`. Returns whether it was.
 * Allocates nothing.
 */
export function readPad(pad: PadLike, map: PadMap, state: AxisState, now: number): boolean {
    let peak = 0;
    const raw = state.raw;
    for (let i = 0; i < 6; i++) {
        let v = pad.axes[map.order[i]];
        if (!(v === v)) v = 0; // NaN reads as rest
        if (v > 1) v = 1;
        else if (v < -1) v = -1;
        v *= map.sign[i];
        raw[i] = v * FULL_SCALE;
        const m = v < 0 ? -v : v;
        if (m > peak) peak = m;
    }
    let bits = 0;
    const n = pad.buttons.length < map.buttons.length ? pad.buttons.length : map.buttons.length;
    for (let i = 0; i < n; i++) {
        const bit = map.buttons[i];
        if (bit >= 0 && bit < 32 && pad.buttons[i].pressed) bits |= 1 << bit;
    }
    state.buttons = bits >>> 0;
    if (peak > MOVE_THRESHOLD) {
        state.lastMotionAt = now;
        return true;
    }
    return false;
}

// ---- the driver's wheel events ----------------------------------------------

/** Wheel events this soon after the cap moved are the vendor driver's (it can turn the
 *  same motion into mouse-wheel zoom), not a hand on the wheel. */
export const WHEEL_GUARD_MS = 150;

/**
 * Whether a wheel event over the viewport is the vendor driver's and should be dropped:
 * only while a 3D mouse source is driving, the cap is off rest, and it moved a moment
 * ago. Anything else -- no device, a resting cap, an ordinary wheel turn -- is the user's.
 */
export function shouldSwallowWheel(sourceActive: boolean, now: number, lastMotionAt: number, deflected: boolean): boolean {
    return sourceActive && deflected && now - lastMotionAt <= WHEEL_GUARD_MS;
}
