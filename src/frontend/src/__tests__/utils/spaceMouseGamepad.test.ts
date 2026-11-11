// The 3D mouse over the Gamepad API: which pad ids count (both browsers' formats),
// how the axes and buttons reach the same state the WebHID path fills, which pad is
// chosen, the source standing by while WebHID drives, the idle poll that wakes an
// on-demand render loop, and when the vendor driver's wheel events are dropped.
// A fake `getGamepads` and fake events stand in for the browser.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
    classifyPad,
    DEFAULT_PAD_MAP,
    padHasAxes,
    parsePadId,
    readPad,
    selectPad,
    shouldSwallowWheel,
    WHEEL_GUARD_MS,
    type PadLike,
    type PadMap,
} from "../../utils/spacemouse/gamepadInput";
import { createGamepadSource, type PadStatus } from "../../utils/spacemouse/gamepadSource";
import { createAxisState, FULL_SCALE, processAxes, resetAxes } from "../../utils/spacemouse/hidReport";

const MOUSE_CHROMIUM = "SpaceMouse Pro (Vendor: 046d Product: c62b)";
const MOUSE_FIREFOX = "256f-c635-SpaceMouse Compact";

function pad(id: string, axes: number[], pressed: number[] = [], connected = true): PadLike {
    const buttons = Array.from({ length: 4 }, (_, i) => ({ pressed: pressed.includes(i) }));
    return { id, connected, axes, buttons };
}

// ---- ids ---------------------------------------------------------------------

test("parses the Chromium id format, with and without the STANDARD GAMEPAD marker", () => {
    assert.deepEqual(parsePadId(MOUSE_CHROMIUM), { vendorId: 0x046d, productId: 0xc62b, name: "SpaceMouse Pro" });
    assert.deepEqual(parsePadId("SpaceNavigator (STANDARD GAMEPAD Vendor: 046d Product: c626)"), {
        vendorId: 0x046d,
        productId: 0xc626,
        name: "SpaceNavigator",
    });
    assert.equal(parsePadId("SpaceMouse Compact (Vendor: 256f Product: c635)")?.vendorId, 0x256f);
});

test("parses the Firefox id format, ids without leading zeros included", () => {
    assert.deepEqual(parsePadId(MOUSE_FIREFOX), { vendorId: 0x256f, productId: 0xc635, name: "SpaceMouse Compact" });
    assert.deepEqual(parsePadId("46d-c626-SpaceNavigator"), { vendorId: 0x46d, productId: 0xc626, name: "SpaceNavigator" });
});

test("ids that are neither format are not parsed", () => {
    for (const id of ["", "-", "--", "Xbox 360 Controller (XInput STANDARD GAMEPAD)", "SpaceMouse Pro", "256f-c635", "Vendor: 256f Product: c635", "x (Vendor: zz6f Product: c635)", "12345-c635-x"]) {
        assert.equal(parsePadId(id), null, JSON.stringify(id));
    }
});

test("3D mice are recognised: current vendor, the legacy products, the driver's virtual joystick", () => {
    assert.equal(classifyPad(MOUSE_CHROMIUM)?.kind, "device");
    assert.equal(classifyPad(MOUSE_FIREFOX)?.kind, "device");
    assert.equal(classifyPad("SpaceMouse Compact (Vendor: 256f Product: 1234)")?.kind, "device");
    assert.equal(classifyPad("Unknown Gamepad (Vendor: beef Product: 046d)")?.kind, "virtual");
    assert.equal(classifyPad("beef-046d-Unknown Gamepad")?.kind, "virtual");
});

test("other gamepads, other Logitech devices and near-miss ids are rejected", () => {
    for (const id of [
        "Logitech Dual Action (STANDARD GAMEPAD Vendor: 046d Product: c216)",
        "046d-c21d-Logitech Gamepad F310",
        "G29 Driving Force Racing Wheel (Vendor: 046d Product: c24f)",
        "Xbox 360 Controller (XInput STANDARD GAMEPAD)",
        "45e-28e-Xbox 360 Wired Controller",
        "beef-046e-Unknown Gamepad",
        "beee-046d-Unknown Gamepad",
        "x (Vendor: beef Product: 1234)",
    ]) {
        assert.equal(classifyPad(id), null, id);
    }
});

test("only a pad with six axes can drive", () => {
    assert.equal(padHasAxes(pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0])), true);
    assert.equal(padHasAxes(pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0, 1])), true);
    assert.equal(padHasAxes(pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0])), false);
});

// ---- choosing a pad ------------------------------------------------------------

test("a device is read before the virtual joystick, whichever is listed first", () => {
    const virtual = pad("beef-046d-Unknown Gamepad", [0, 0, 0, 0, 0, 0]);
    const device = pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0]);
    assert.equal(selectPad([virtual, device])?.pad, device);
    assert.equal(selectPad([device, virtual])?.pad, device);
    assert.equal(selectPad([virtual])?.pad, virtual);
});

test("a usable pad beats a recognised one without axes; a lone one without axes is reported as such", () => {
    const bare = pad(MOUSE_CHROMIUM, [0, 0]);
    const full = pad(MOUSE_FIREFOX, [0, 0, 0, 0, 0, 0]);
    assert.equal(selectPad([bare, full])?.pad, full);
    const only = selectPad([bare]);
    assert.equal(only?.kind, "noAxes");
    assert.equal(only?.info.name, "SpaceMouse Pro");
});

test("a virtual joystick without six axes is just a joystick; unrelated and disconnected pads are skipped", () => {
    assert.equal(selectPad([pad("beef-046d-Unknown Gamepad", [0, 0])]), null);
    assert.equal(selectPad([pad("Xbox 360 Controller (XInput STANDARD GAMEPAD)", [0, 0, 0, 0, 0, 0])]), null);
    assert.equal(selectPad([pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0], [], false)]), null);
    assert.equal(selectPad([null, undefined]), null);
    assert.equal(selectPad(null), null);
});

// ---- axes ----------------------------------------------------------------------

test("axes reach the HID path's state in its units, so processAxes shapes them the same", () => {
    const state = createAxisState();
    const moved = readPad(pad(MOUSE_CHROMIUM, [1, -1, 0.5, -0.5, 0.25, 0]), DEFAULT_PAD_MAP, state, 1234);
    assert.equal(moved, true);
    assert.deepEqual(Array.from(state.raw), [FULL_SCALE, -FULL_SCALE, FULL_SCALE / 2, -FULL_SCALE / 2, FULL_SCALE / 4, 0]);
    assert.equal(state.lastMotionAt, 1234);

    // The same device-frame values a HID report would have written give the same view-frame motion.
    const out = new Float32Array(6);
    const settings = { deadZone: 0, invert: [false, false, false, false, false, false], dominantAxis: false, lockRotation: false, lockPan: false, enableRoll: true };
    assert.equal(processAxes(state.raw, settings, out), true);
    assert.deepEqual(Array.from(out), [1, -0.5, -1, -0.5, 0, 0.25]);
});

test("the per-device map's order and sign are applied; out-of-range and NaN readings are clamped", () => {
    const map: PadMap = {
        order: [1, 0, 2, 3, 4, 5],
        sign: [1, -1, 1, 1, 1, -1],
        buttons: DEFAULT_PAD_MAP.buttons,
    };
    const state = createAxisState();
    readPad(pad(MOUSE_CHROMIUM, [0.2, 0.4, 5, Number.NaN, -9, 0.5]), map, state, 1);
    // tx <- axes[1], ty <- -axes[0], tz <- axes[2] (clamped), rx <- axes[3] (NaN), ry <- axes[4] (clamped), rz <- -axes[5]
    assert.deepEqual(
        Array.from(state.raw).map((v) => Math.round(v)),
        [0.4 * FULL_SCALE, -0.2 * FULL_SCALE, FULL_SCALE, 0, -FULL_SCALE, -0.5 * FULL_SCALE].map((v) => Math.round(v)),
    );
});

test("a cap at rest is not motion, and does not refresh the stale clock", () => {
    const state = createAxisState();
    assert.equal(readPad(pad(MOUSE_CHROMIUM, [0.005, 0, 0, 0, -0.01, 0]), DEFAULT_PAD_MAP, state, 99), false);
    assert.equal(state.lastMotionAt, -Infinity);
});

test("buttons map pad.buttons[i] to our bit i, and an entry of -1 is ignored", () => {
    const state = createAxisState();
    readPad(pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0], [0, 2]), DEFAULT_PAD_MAP, state, 1);
    assert.equal(state.buttons, 0b101);
    const swapped: PadMap = { ...DEFAULT_PAD_MAP, buttons: [1, 0, -1, 3] };
    readPad(pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0], [0, 2]), swapped, state, 2);
    assert.equal(state.buttons, 0b10);
});

// ---- the source ----------------------------------------------------------------

class FakeTarget {
    listeners = new Map<string, Set<() => void>>();
    hidden = false;
    addEventListener(type: string, fn: () => void) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type)!.add(fn);
    }
    removeEventListener(type: string, fn: () => void) {
        this.listeners.get(type)?.delete(fn);
    }
    fire(type: string) {
        for (const fn of [...(this.listeners.get(type) ?? [])]) fn();
    }
    count(type: string) {
        return this.listeners.get(type)?.size ?? 0;
    }
}

function rig() {
    const pads: (PadLike | null)[] = [];
    const win = new FakeTarget();
    const doc = new FakeTarget();
    const axis = createAxisState();
    const timers = new Map<number, () => void>();
    let nextTimer = 1;
    const wakes: number[] = [];
    const pressed: number[] = [];
    const statuses: PadStatus[] = [];
    let clock = 1000;
    const source = createGamepadSource({
        axis,
        getGamepads: () => pads,
        target: win as unknown as EventTarget,
        doc: doc as unknown as EventTarget & { readonly hidden: boolean },
        onWake: () => wakes.push(clock),
        onButtons: (b) => pressed.push(b),
        onStatus: (s) => statuses.push(s),
        now: () => clock,
        setInterval: (fn) => {
            timers.set(nextTimer, fn);
            return nextTimer++;
        },
        clearInterval: (h) => void timers.delete(h as number),
    });
    return {
        pads, win, doc, axis, timers, wakes, pressed, statuses, source,
        tick: () => timers.forEach((fn) => fn()),
        at: (t: number) => (clock = t),
    };
}

test("polling writes the axes and reports motion", () => {
    const r = rig();
    r.source.start();
    assert.equal(r.source.poll(), false, "no pad listed yet");
    r.pads.push(pad(MOUSE_CHROMIUM, [0.5, 0, 0, 0, 0, 0]));
    assert.equal(r.source.poll(), true);
    assert.equal(r.axis.raw[0], 0.5 * FULL_SCALE);
    assert.equal(r.source.status().kind, "active");
    assert.equal(r.source.driving(), true);
});

test("nothing is read before start, after stop, or while suspended (WebHID drives)", () => {
    const r = rig();
    r.pads.push(pad(MOUSE_CHROMIUM, [1, 0, 0, 0, 0, 0]));
    assert.equal(r.source.poll(), false, "not started");
    r.source.start();
    r.source.setSuspended(true);
    assert.equal(r.source.poll(), false);
    assert.equal(r.axis.raw[0], 0, "suspended: the axes are not touched");
    assert.equal(r.source.driving(), false);
    r.source.setSuspended(false);
    assert.equal(r.source.poll(), true, "resumes when WebHID goes away");
    r.source.stop();
    r.axis.raw.fill(0);
    assert.equal(r.source.poll(), false, "stopped");
    assert.equal(r.axis.raw[0], 0);
});

test("priority: while WebHID drives, the pad's readings do not overwrite its axes; the pad resumes after", () => {
    const r = rig();
    r.pads.push(pad(MOUSE_CHROMIUM, [-1, 0, 0, 0, 0, 0]));
    r.source.start();
    r.source.setSuspended(true); // a WebHID device opened
    r.axis.raw[0] = 123; // what its report wrote
    r.source.poll();
    r.tick();
    assert.equal(r.axis.raw[0], 123);
    r.source.setSuspended(false); // the device went away
    resetAxes(r.axis);
    r.source.poll();
    assert.equal(r.axis.raw[0], -FULL_SCALE);
});

test("buttons that went down are passed on once", () => {
    const r = rig();
    r.source.start();
    r.pads.push(pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0], [1]));
    r.source.poll();
    r.source.poll();
    assert.deepEqual(r.pressed, [0b10]);
    r.pads[0] = pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0], []);
    r.source.poll();
    r.pads[0] = pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0], [0, 1]);
    r.source.poll();
    assert.deepEqual(r.pressed, [0b10, 0b11]);
});

test("a recognised pad without axes is a status of its own, not silence", () => {
    const r = rig();
    r.source.start();
    r.pads.push(pad(MOUSE_CHROMIUM, [0, 0]));
    r.source.poll();
    assert.deepEqual(r.source.status(), { kind: "noAxes", name: "SpaceMouse Pro", vendorId: 0x046d, productId: 0xc62b });
    assert.equal(r.source.driving(), false);
    assert.equal(r.statuses.at(-1)?.kind, "noAxes");
});

test("status changes are reported once each, and gamepadconnected / disconnected rescan", () => {
    const r = rig();
    r.source.start();
    assert.equal(r.statuses.length, 0, "none at the start is not news");
    r.pads.push(pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0]));
    r.win.fire("gamepadconnected");
    assert.deepEqual(r.statuses.map((s) => s.kind), ["active"]);
    r.source.poll();
    r.source.poll();
    assert.equal(r.statuses.length, 1);
    r.pads.length = 0;
    r.win.fire("gamepaddisconnected");
    assert.deepEqual(r.statuses.map((s) => s.kind), ["active", "none"]);
});

test("a pad that goes away mid-push stops the camera: its readings are cleared", () => {
    const r = rig();
    r.source.start();
    r.pads.push(pad(MOUSE_CHROMIUM, [1, 0, 0, 0, 0, 0]));
    r.source.poll();
    assert.equal(r.axis.raw[0], FULL_SCALE);
    r.pads.length = 0;
    r.win.fire("gamepaddisconnected");
    assert.equal(r.axis.raw[0], 0);
});

test("an unfocused page reads nothing from the pad", () => {
    const axis = createAxisState();
    const source = createGamepadSource({
        axis,
        getGamepads: () => [pad(MOUSE_CHROMIUM, [1, 0, 0, 0, 0, 0])],
        target: null,
        onWake: () => {},
        onButtons: () => {},
        onStatus: () => {},
        isFocused: () => false,
    });
    source.start();
    assert.equal(source.poll(), false);
    assert.equal(axis.raw[0], 0);
    source.stop();
});

// ---- the idle poll ---------------------------------------------------------------

test("the idle poll runs only while started, not suspended and visible; it wakes the loop on motion only", () => {
    const r = rig();
    assert.equal(r.source.idleTimerRunning(), false);
    r.source.start();
    assert.equal(r.source.idleTimerRunning(), true);
    assert.equal(r.timers.size, 1);

    r.tick();
    assert.equal(r.wakes.length, 0, "no pad: quiet");
    r.pads.push(pad(MOUSE_CHROMIUM, [0, 0, 0, 0, 0, 0]));
    r.tick();
    assert.equal(r.wakes.length, 0, "a pad at rest: quiet");
    r.pads[0] = pad(MOUSE_CHROMIUM, [0, 0.6, 0, 0, 0, 0]);
    r.at(2000);
    r.tick();
    assert.deepEqual(r.wakes, [2000], "the cap moved: one wake");
});

test("the idle poll stops when the tab is hidden and when the source is suspended or stopped", () => {
    const r = rig();
    r.source.start();
    r.doc.hidden = true;
    r.doc.fire("visibilitychange");
    assert.equal(r.source.idleTimerRunning(), false);
    assert.equal(r.timers.size, 0);
    r.doc.hidden = false;
    r.doc.fire("visibilitychange");
    assert.equal(r.source.idleTimerRunning(), true);

    r.source.setSuspended(true);
    assert.equal(r.timers.size, 0);
    r.source.setSuspended(false);
    assert.equal(r.timers.size, 1);

    r.source.stop();
    assert.equal(r.timers.size, 0);
    assert.equal(r.win.count("gamepadconnected"), 0, "its listeners are gone");
    assert.equal(r.win.count("gamepaddisconnected"), 0);
    assert.equal(r.doc.count("visibilitychange"), 0);
    // And starting again starts exactly one timer.
    r.source.start();
    r.source.start();
    assert.equal(r.timers.size, 1);
});

// ---- the vendor driver's wheel events --------------------------------------------

test("wheel events are dropped only while a source drives, the cap is off rest and it moved a moment ago", () => {
    assert.equal(shouldSwallowWheel(true, 1000, 1000 - 10, true), true);
    assert.equal(shouldSwallowWheel(true, 1000, 1000 - WHEEL_GUARD_MS, true), true, "at the edge");
    assert.equal(shouldSwallowWheel(true, 1000, 1000 - WHEEL_GUARD_MS - 1, true), false, "motion is old: a hand on the wheel");
    assert.equal(shouldSwallowWheel(true, 1000, 1000 - 10, false), false, "cap at rest");
    assert.equal(shouldSwallowWheel(false, 1000, 1000 - 10, true), false, "no source: an ordinary mouse wheel");
    assert.equal(shouldSwallowWheel(true, 1000, -Infinity, true), false, "never moved");
});
