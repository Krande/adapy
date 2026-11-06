// 3D-mouse support turning itself on.
//
// Until the user has chosen in Options (`chosen`), a device this page may already
// use switches support on: `navigator.hid.getDevices()` lists only devices the
// site was granted before -- or, in the desktop app, the native reader's device --
// so looking never prompts. A device plugged in later counts too. Kept apart from
// ./spaceMouseDevice (which pulls in the scene) so it can be tested on its own.

import { useSpaceMouseStore } from "@/state/spaceMouseStore";

import { isSpaceMouse } from "./hidReport";

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

let gen = 0;
let watching: AutoHid | null = null;

function onConnect(event: Event): void {
    const d = (event as unknown as { device?: AutoHidDevice }).device;
    if (d && isSpaceMouse(d.vendorId, d.productId)) useSpaceMouseStore.getState().autoEnable();
}

/** Stop looking (support was switched on, or the user chose). */
export function stopAutoDetect(): void {
    gen++;
    watching?.removeEventListener("connect", onConnect);
    watching = null;
}

/** Look for a usable device while support is off and not chosen; on finding one, switch it on. */
export function startAutoDetect(): void {
    const s = useSpaceMouseStore.getState();
    if (s.chosen || s.enabled || watching) return;
    const hid = getHid();
    if (!hid) return;
    watching = hid;
    const mine = ++gen;
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
