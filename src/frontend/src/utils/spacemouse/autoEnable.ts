// 3D-mouse support turning itself on.
//
// Until the user has chosen in Options (`chosen`), a device this page may already
// use switches support on, and looking never prompts:
//   * `navigator.hid.getDevices()` lists only devices the site was granted before --
//     or, in the desktop app, the native reader's device; a device plugged in later
//     counts too;
//   * `navigator.getGamepads()` lists a 3D mouse (as a joystick-class pad) once it has
//     been moved on this page, and `gamepadconnected` fires at that moment, so a user
//     who has never granted anything is switched on by their first push of the cap.
// Switching on goes through `autoEnable()`, which leaves a user who chose off alone and
// does not count as a choice. Kept apart from ./spaceMouseDevice (which pulls in the
// scene) so it can be tested on its own.

import {useSpaceMouseStore} from "@/state/spaceMouseStore";

import {classifyPad, type PadLike} from "./gamepadInput";
import {isSpaceMouse} from "./hidReport";

interface AutoHidDevice {
    vendorId: number;
    productId: number;
}

interface AutoHid {
    getDevices(): Promise<AutoHidDevice[]>;
    addEventListener(type: "connect", listener: (e: Event) => void): void;
    removeEventListener(type: "connect", listener: (e: Event) => void): void;
}

function getHid(): AutoHid | null {
    if (typeof navigator === "undefined") return null;
    return (navigator as unknown as { hid?: AutoHid }).hid ?? null;
}

function listedPads(): ArrayLike<PadLike | null> | null {
    if (typeof navigator === "undefined") return null;
    const nav = navigator as unknown as { getGamepads?: () => ArrayLike<PadLike | null> };
    try {
        return nav.getGamepads?.() ?? null;
    } catch {
        return null;
    }
}

function padListed(): boolean {
    const pads = listedPads();
    if (!pads) return false;
    for (let i = 0; i < pads.length; i++) {
        const p = pads[i];
        if (p && p.connected !== false && classifyPad(p.id)) return true;
    }
    return false;
}

function padTarget(): EventTarget | null {
    return typeof window !== "undefined" ? window : null;
}

let gen = 0;
let watching = false;
let watchedHid: AutoHid | null = null;
let watchedPads: EventTarget | null = null;

function onConnect(event: Event): void {
    const d = (event as unknown as { device?: AutoHidDevice }).device;
    if (d && isSpaceMouse(d.vendorId, d.productId)) useSpaceMouseStore.getState().autoEnable();
}

function onPadConnected(event: Event): void {
    const pad = (event as unknown as { gamepad?: PadLike }).gamepad;
    if (pad && classifyPad(pad.id)) useSpaceMouseStore.getState().autoEnable();
}

/** Stop looking (support was switched on, or the user chose). */
export function stopAutoDetect(): void {
    gen++;
    watchedHid?.removeEventListener("connect", onConnect);
    watchedPads?.removeEventListener("gamepadconnected", onPadConnected);
    watchedHid = null;
    watchedPads = null;
    watching = false;
}

/** Look for a usable device while support is off and not chosen; on finding one, switch it on. */
export function startAutoDetect(): void {
    const s = useSpaceMouseStore.getState();
    if (s.chosen || s.enabled || watching) return;
    const hid = getHid();
    const pads = padTarget();
    if (!hid && !pads) return;
    watching = true;
    const mine = ++gen;
    if (pads) {
        watchedPads = pads;
        pads.addEventListener("gamepadconnected", onPadConnected);
        // A pad the browser already lists (it was moved before the page looked).
        if (padListed()) useSpaceMouseStore.getState().autoEnable();
    }
    // Switching on may have stopped the look already (a subscriber), so look again before the HID half.
    if (!hid || !watching || mine !== gen) return;
    watchedHid = hid;
    hid.addEventListener("connect", onConnect);
    void hid
        .getDevices()
        .then((devices) => {
            if (mine === gen && devices.some((d) => isSpaceMouse(d.vendorId, d.productId))) {
                useSpaceMouseStore.getState().autoEnable();
            }
        })
        .catch(() => {
            // Nothing to find (a permissions policy, say): stay off, quietly.
        });
}

/** Keep looking for as long as support is off and not chosen. Returns the stop. */
export function followAutoEnable(): () => void {
    startAutoDetect();
    const unsubscribe = useSpaceMouseStore.subscribe((s) => {
        if (s.chosen || s.enabled) stopAutoDetect();
        else startAutoDetect();
    });
    return () => {
        unsubscribe();
        stopAutoDetect();
    };
}
