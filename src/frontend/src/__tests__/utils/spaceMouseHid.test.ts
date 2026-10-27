// The 3D-mouse HID path: which devices count, what each report layout says, how
// the device frame maps onto the view frame, and how the dead zone, inverts and
// locks shape the result. All of it is wrong silently if wrong -- the camera
// just moves the wrong way -- hence pinned here.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
    applyInputReport,
    createAxisState,
    FULL_SCALE,
    HID_FILTERS,
    isSpaceMouse,
    LEGACY_VENDOR_ID,
    processAxes,
    REPORT_BUTTONS,
    REPORT_MOTION,
    REPORT_NONE,
    resetAxes,
    takePressedButtons,
    VENDOR_ID,
    type AxisSettings,
} from "../../utils/spacemouse/hidReport";

const NEUTRAL: AxisSettings = {
    deadZone: 0,
    invert: [false, false, false, false, false, false],
    dominantAxis: false,
    lockRotation: false,
    lockPan: false,
};

function report(...values: number[]): DataView {
    const buf = new ArrayBuffer(values.length * 2);
    const view = new DataView(buf);
    values.forEach((v, i) => view.setInt16(i * 2, v, true));
    return view;
}

function bytes(...values: number[]): DataView {
    return new DataView(new Uint8Array(values).buffer);
}

test("every current-vendor product is a 3D mouse; under the older vendor id only the listed ones", () => {
    assert.ok(isSpaceMouse(VENDOR_ID, 0xc635));
    assert.ok(isSpaceMouse(VENDOR_ID, 0x1234));
    assert.ok(isSpaceMouse(LEGACY_VENDOR_ID, 0xc626)); // SpaceNavigator
    assert.ok(!isSpaceMouse(LEGACY_VENDOR_ID, 0xc52b)); // an ordinary receiver
    assert.ok(!isSpaceMouse(0x1234, 0xc626));
    // The chooser filter never offers the older vendor id without a product id:
    // that would list every mouse and keyboard sold under it.
    for (const f of HID_FILTERS) {
        if (f.vendorId === LEGACY_VENDOR_ID) assert.ok(f.productId !== undefined);
    }
});

test("split layout: report 1 is translation, report 2 rotation, little-endian signed", () => {
    const s = createAxisState();
    assert.equal(applyInputReport(s, 1, report(10, -20, 350), 5), REPORT_MOTION);
    assert.deepEqual([...s.raw], [10, -20, 350, 0, 0, 0]);
    assert.equal(s.lastMotionAt, 5);
    assert.equal(applyInputReport(s, 2, report(-1, 2, -350), 6), REPORT_MOTION);
    assert.deepEqual([...s.raw], [10, -20, 350, -1, 2, -350]);
    // A translation-only report leaves the rotation from report 2 alone.
    applyInputReport(s, 1, report(0, 0, 0), 7);
    assert.deepEqual([...s.raw], [0, 0, 0, -1, 2, -350]);
});

test("combined layout: one 12-byte report 1 carries all six axes", () => {
    const s = createAxisState();
    assert.equal(applyInputReport(s, 1, report(1, 2, 3, -4, -5, -6), 1), REPORT_MOTION);
    assert.deepEqual([...s.raw], [1, 2, 3, -4, -5, -6]);
});

test("short or unknown reports are ignored", () => {
    const s = createAxisState();
    assert.equal(applyInputReport(s, 1, bytes(1, 2), 1), REPORT_NONE);
    assert.equal(applyInputReport(s, 2, bytes(1, 2, 3), 1), REPORT_NONE);
    assert.equal(applyInputReport(s, 0x17, report(1, 2, 3), 1), REPORT_NONE);
    assert.deepEqual([...s.raw], [0, 0, 0, 0, 0, 0]);
    assert.equal(s.lastMotionAt, -Infinity);
});

test("buttons: report 3 is a bitmask, and only the press edge fires", () => {
    const s = createAxisState();
    assert.equal(applyInputReport(s, 3, bytes(0b01), 1), REPORT_BUTTONS);
    assert.equal(takePressedButtons(s), 0b01);
    assert.equal(takePressedButtons(s), 0, "held is not pressed again");
    applyInputReport(s, 3, bytes(0b11), 2);
    assert.equal(takePressedButtons(s), 0b10, "button 2 joins while 1 is held");
    applyInputReport(s, 3, bytes(0), 3);
    assert.equal(takePressedButtons(s), 0, "release is not a press");
    applyInputReport(s, 3, bytes(0, 0, 0, 0x80), 4);
    assert.equal(takePressedButtons(s), 0x80000000, "bit 31 stays unsigned");
});

test("device frame -> view frame: x right, push down = -y (down), pull toward user = +z", () => {
    const out = new Float32Array(6);
    // device: +x right, +y toward user, +z down
    processAxes([FULL_SCALE, 0, 0, 0, 0, 0], NEUTRAL, out);
    assert.deepEqual([...out], [1, 0, 0, 0, 0, 0]);
    processAxes([0, 0, FULL_SCALE, 0, 0, 0], NEUTRAL, out);
    assert.deepEqual([...out].map((v) => v + 0), [0, -1, 0, 0, 0, 0]);
    processAxes([0, FULL_SCALE, 0, 0, 0, 0], NEUTRAL, out);
    assert.deepEqual([...out].map((v) => v + 0), [0, 0, 1, 0, 0, 0]);
    // rotations map the same way: device rz (about down) is view -ry (about up)
    processAxes([0, 0, 0, 0, 0, FULL_SCALE], NEUTRAL, out);
    assert.deepEqual([...out].map((v) => v + 0), [0, 0, 0, 0, -1, 0]);
    processAxes([0, 0, 0, 0, FULL_SCALE, 0], {...NEUTRAL, enableRoll: true}, out);
    assert.deepEqual([...out].map((v) => v + 0), [0, 0, 0, 0, 0, 1]);
});

test("roll is dropped unless enabled, and then cannot win the dominant axis", () => {
    const out = new Float32Array(6);
    assert.equal(processAxes([0, 0, 0, 0, FULL_SCALE, 0], NEUTRAL, out), false);
    processAxes([100, 0, 0, 0, 300, 0], {...NEUTRAL, dominantAxis: true}, out);
    assert.deepEqual([...out].map((v) => v + 0), [100 / FULL_SCALE, 0, 0, 0, 0, 0].map((v) => Math.fround(v)));
});

test("values are clamped to full scale", () => {
    const out = new Float32Array(6);
    processAxes([2 * FULL_SCALE, 0, 0, -2 * FULL_SCALE, 0, 0], NEUTRAL, out);
    assert.equal(out[0], 1);
    assert.equal(out[3], -1);
});

test("dead zone: rest inside it, continuous at its edge, full speed at full deflection", () => {
    const out = new Float32Array(6);
    const s = {...NEUTRAL, deadZone: 0.1};
    assert.equal(processAxes([0.1 * FULL_SCALE, 0, 0, 0, 0, 0], s, out), false);
    assert.equal(out[0], 0);
    processAxes([0.1 * FULL_SCALE + 0.35, 0, 0, 0, 0, 0], s, out);
    assert.ok(out[0] > 0 && out[0] < 0.01, `just past the edge is small, got ${out[0]}`);
    processAxes([0.55 * FULL_SCALE, 0, 0, 0, 0, 0], s, out);
    assert.ok(Math.abs(out[0] - 0.5) < 1e-6);
    processAxes([-FULL_SCALE, 0, 0, 0, 0, 0], s, out);
    assert.equal(out[0], -1);
});

test("all at rest reports false, so the caller can stop", () => {
    const out = new Float32Array(6);
    assert.equal(processAxes([0, 0, 0, 0, 0, 0], NEUTRAL, out), false);
});

test("invert flips one view axis", () => {
    const out = new Float32Array(6);
    const s = {...NEUTRAL, invert: [false, false, false, false, true, false]};
    processAxes([0, 0, 0, 0, 0, FULL_SCALE], s, out);
    assert.equal(out[4], 1);
});

test("locks drop the rotation or the translation axes", () => {
    const out = new Float32Array(6);
    const raw = [FULL_SCALE, FULL_SCALE, FULL_SCALE, FULL_SCALE, FULL_SCALE, FULL_SCALE];
    processAxes(raw, {...NEUTRAL, lockRotation: true}, out);
    assert.deepEqual([...out.slice(3)], [0, 0, 0]);
    assert.ok(out[0] !== 0 && out[1] !== 0 && out[2] !== 0);
    processAxes(raw, {...NEUTRAL, lockPan: true}, out);
    assert.deepEqual([...out.slice(0, 3)], [0, 0, 0]);
    // A locked-only push is rest.
    assert.equal(processAxes([FULL_SCALE, 0, 0, 0, 0, 0], {...NEUTRAL, lockPan: true}, out), false);
});

test("dominant axis keeps only the hardest push", () => {
    const out = new Float32Array(6);
    processAxes([100, 0, 0, 300, 0, 0], {...NEUTRAL, dominantAxis: true}, out);
    assert.deepEqual([...out].map((v) => v + 0), [0, 0, 0, 300 / FULL_SCALE, 0, 0].map((v) => Math.fround(v)));
});

test("resetAxes forgets motion and buttons", () => {
    const s = createAxisState();
    applyInputReport(s, 1, report(1, 2, 3, 4, 5, 6), 1);
    applyInputReport(s, 3, bytes(1), 1);
    resetAxes(s);
    assert.deepEqual([...s.raw], [0, 0, 0, 0, 0, 0]);
    assert.equal(s.buttons, 0);
    assert.equal(s.lastMotionAt, -Infinity);
});

test("hot path: parse + process reuse their buffers and stay cheap", () => {
    const s = createAxisState();
    const out = new Float32Array(6);
    const rawBefore = s.raw;
    const combined = report(120, -40, 300, 10, -200, 5);
    const settings = {...NEUTRAL, deadZone: 0.08};
    const N = 100_000;
    // warm up the JIT before timing
    for (let i = 0; i < 1000; i++) {
        applyInputReport(s, 1, combined, i);
        processAxes(s.raw, settings, out);
    }
    const t0 = performance.now();
    for (let i = 0; i < N; i++) {
        applyInputReport(s, 1, combined, i);
        processAxes(s.raw, settings, out);
    }
    const perCallUs = ((performance.now() - t0) * 1000) / N;
    assert.equal(s.raw, rawBefore, "the state's array is written in place, never replaced");
    // Generous bound so a slow CI box passes; locally this is well under 0.1 us.
    assert.ok(perCallUs < 5, `parse + process took ${perCallUs.toFixed(3)} us per report`);
    console.log(`spacemouse parse+process: ${perCallUs.toFixed(4)} us/report over ${N} reports`);
});
