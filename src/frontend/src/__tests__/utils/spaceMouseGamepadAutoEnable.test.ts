// A 3D mouse listed through the Gamepad API turns support on, like a granted WebHID
// device does: through `autoEnable()`, so a user who chose off keeps it off, and
// without counting as a choice. Looking never asks the browser for anything.

import assert from "node:assert/strict";
import { test } from "node:test";

const pads: unknown[] = [];
const win = new EventTarget();
Object.defineProperty(globalThis, "navigator", { value: { getGamepads: () => pads }, configurable: true, writable: true });
Object.defineProperty(globalThis, "window", { value: win, configurable: true, writable: true });
const mem = new Map<string, string>();
Object.defineProperty(globalThis, "localStorage", {
    value: {
        getItem: (k: string) => mem.get(k) ?? null,
        setItem: (k: string, v: string) => void mem.set(k, v),
        removeItem: (k: string) => void mem.delete(k),
    },
    configurable: true,
    writable: true,
});

const { useSpaceMouseStore } = await import("../../state/spaceMouseStore");
const { followAutoEnable } = await import("../../utils/spacemouse/autoEnable");

const MOUSE = { id: "SpaceMouse Pro (Vendor: 046d Product: c62b)", connected: true, axes: [0, 0, 0, 0, 0, 0], buttons: [] };
const XBOX = { id: "Xbox 360 Controller (XInput STANDARD GAMEPAD)", connected: true, axes: [0, 0, 0, 0], buttons: [] };

function reset(state: { enabled: boolean; chosen: boolean }) {
    useSpaceMouseStore.setState(state);
    pads.length = 0;
}

function connected(gamepad: unknown) {
    win.dispatchEvent(Object.assign(new Event("gamepadconnected"), { gamepad }));
}

test("no pad: support stays off and nothing is chosen", () => {
    reset({ enabled: false, chosen: false });
    const stop = followAutoEnable();
    assert.equal(useSpaceMouseStore.getState().enabled, false);
    assert.equal(useSpaceMouseStore.getState().chosen, false);
    stop();
});

test("a pad the browser already lists turns support on, without counting as a choice", () => {
    reset({ enabled: false, chosen: false });
    pads.push(MOUSE);
    const stop = followAutoEnable();
    assert.equal(useSpaceMouseStore.getState().enabled, true);
    assert.equal(useSpaceMouseStore.getState().chosen, false);
    stop();
});

test("the first push of the cap (gamepadconnected) turns it on too", () => {
    reset({ enabled: false, chosen: false });
    const stop = followAutoEnable();
    assert.equal(useSpaceMouseStore.getState().enabled, false);
    connected(MOUSE);
    assert.equal(useSpaceMouseStore.getState().enabled, true);
    assert.equal(useSpaceMouseStore.getState().chosen, false);
    stop();
});

test("an ordinary gamepad does not", () => {
    reset({ enabled: false, chosen: false });
    pads.push(XBOX);
    const stop = followAutoEnable();
    connected(XBOX);
    assert.equal(useSpaceMouseStore.getState().enabled, false);
    stop();
});

test("switched off in Options, a listed or connecting pad leaves it off", () => {
    reset({ enabled: false, chosen: false });
    useSpaceMouseStore.getState().setEnabled(false);
    assert.equal(useSpaceMouseStore.getState().chosen, true);
    pads.push(MOUSE);
    const stop = followAutoEnable();
    connected(MOUSE);
    assert.equal(useSpaceMouseStore.getState().enabled, false);
    stop();
});

test("after stopping, a connecting pad no longer matters", () => {
    reset({ enabled: false, chosen: false });
    const stop = followAutoEnable();
    stop();
    connected(MOUSE);
    assert.equal(useSpaceMouseStore.getState().enabled, false);
});
