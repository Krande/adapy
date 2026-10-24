import {test} from "node:test";
import assert from "node:assert/strict";

import {FULL_SCALE, processAxes} from "../../utils/spacemouse/hidReport";
import {createOneEuro, stepOneEuro} from "../../utils/spacemouse/smoothing";

const DT = 1 / 60;
const SETTINGS = {
    deadZone: 0.08,
    invert: [false, false, false, false, false, false],
    dominantAxis: false,
    lockRotation: false,
    lockPan: false,
};

// A gentle push: just past the dead zone (0.08 of 350 = 28 counts), with a
// 10 Hz hand tremor and a little sensor noise riding on it.
function gentlePush(t: number, seed: {s: number}): number {
    seed.s = (seed.s * 1103515245 + 12345) % 2147483648;
    const noise = (seed.s / 2147483648 - 0.5) * 4;
    return 40 + 6 * Math.sin(2 * Math.PI * 10 * t) + noise;
}

/** Relative frame-to-frame spread of the processed speed on axis tx. */
function speedSpread(filtered: boolean): number {
    const f = createOneEuro(6);
    const raw = new Float32Array(6);
    const smooth = new Float32Array(6);
    const motion = new Float32Array(6);
    const seed = {s: 7};
    const speeds: number[] = [];
    for (let i = 0; i < 240; i++) {
        raw[0] = gentlePush(i * DT, seed);
        stepOneEuro(f, raw, DT, FULL_SCALE, smooth);
        processAxes(filtered ? smooth : raw, SETTINGS, motion);
        if (i >= 60) speeds.push(motion[0]); // after a second of settling
    }
    const mean = speeds.reduce((a, b) => a + b, 0) / speeds.length;
    const sd = Math.sqrt(speeds.reduce((a, b) => a + (b - mean) ** 2, 0) / speeds.length);
    return sd / mean;
}

test("a gentle push with tremor glides at a steady speed", () => {
    const raw = speedSpread(false);
    const smooth = speedSpread(true);
    // Unfiltered, the tremor swings the speed by a third or more of itself.
    assert.ok(raw > 0.3, `raw spread ${raw}`);
    assert.ok(smooth < raw / 4, `filtered spread ${smooth} vs raw ${raw}`);
});

test("a firm push and its release still respond quickly", () => {
    const f = createOneEuro(1);
    const out = new Float32Array(1);
    const push = new Float32Array([200]);
    let t90 = -1;
    for (let i = 0; i < 60; i++) {
        stepOneEuro(f, push, DT, FULL_SCALE, out);
        if (t90 < 0 && out[0] >= 180) t90 = (i + 1) * DT;
    }
    assert.ok(t90 > 0 && t90 <= 0.2, `reached 90% after ${t90}s`);

    const rest = new Float32Array(1);
    let tStop = -1;
    for (let i = 0; i < 60; i++) {
        stepOneEuro(f, rest, DT, FULL_SCALE, out);
        // Below the dead zone counts as stopped.
        if (tStop < 0 && out[0] <= 0.08 * FULL_SCALE) tStop = (i + 1) * DT;
    }
    assert.ok(tStop > 0 && tStop <= 0.25, `stopped after ${tStop}s`);
});
