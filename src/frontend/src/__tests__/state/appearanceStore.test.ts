// The appearance settings every renderer reads: their defaults are the look the viewer had before
// they existed, and each renderer is handed the same two light numbers.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

const memory = new Map<string, string>();
(globalThis as unknown as Record<string, unknown>).localStorage = {
    getItem: (k: string) => memory.get(k) ?? null,
    setItem: (k: string, v: string) => void memory.set(k, String(v)),
    removeItem: (k: string) => void memory.delete(k),
};

const A = await import("@/state/appearanceStore");
const store = () => A.useAppearanceStore.getState();

beforeEach(() => store().reset());

test("the defaults are the lights, the background and the grey the viewer always had", () => {
    assert.deepEqual(A.lightIntensities(), {ambient: Math.PI / 2, headlight: 1.4});
    assert.equal(store().background, "#393939");
    assert.deepEqual(A.defaultColorLinear(), [0.8, 0.8, 0.8]);
    // #e7e7e7 is what 0.8 linear looks like on screen.
    A.hexLinear(A.APPEARANCE_DEFAULTS.defaultColor).forEach((c) => assert.ok(Math.abs(c - 0.8) < 0.005, String(c)));
    assert.equal(store().shadeResults, false);
    assert.equal(A.appearanceIsDefault(), true);
});

test("brightness multiplies both lights, the balance moves light between them", () => {
    store().setBrightness(1.5);
    const bright = A.lightIntensities();
    assert.ok(Math.abs(bright.ambient - 1.5 * (Math.PI / 2)) < 1e-9);
    assert.ok(Math.abs(bright.headlight - 1.5 * 1.4) < 1e-9);
    store().setBrightness(1);
    store().setAmbientShare(1);
    assert.deepEqual(A.lightIntensities(), {ambient: Math.PI / 2 + 1.4, headlight: 0});
    store().setAmbientShare(0.25);
    const {ambient, headlight} = A.lightIntensities();
    assert.ok(Math.abs(ambient + headlight - (Math.PI / 2 + 1.4)) < 1e-9, "the balance keeps the total");
    assert.ok(Math.abs(ambient / (ambient + headlight) - 0.25) < 1e-9);
    assert.equal(A.appearanceIsDefault(), false);
});

test("a value that is not one is refused, and reset goes back to the defaults", () => {
    store().setBrightness(99);
    assert.equal(store().brightness, A.BRIGHTNESS_RANGE.max);
    store().setBrightness(Number.NaN);
    assert.equal(store().brightness, A.BRIGHTNESS_RANGE.max);
    store().setDefaultColor("red");
    assert.equal(store().defaultColor, A.APPEARANCE_DEFAULTS.defaultColor);
    store().setDefaultColor("#3366CC");
    assert.equal(store().defaultColor, "#3366cc");
    store().setBackground("#ffffff");
    store().setShadeResults(true);
    store().reset();
    assert.equal(A.appearanceIsDefault(), true);
    assert.deepEqual(A.sanitizeAppearance({brightness: "2", background: "#12", ambientShare: 7, shadeResults: 1}), {
        ...A.APPEARANCE_DEFAULTS,
        ambientShare: 1,
    });
});

test("the settings are stored under their own key", () => {
    store().setBrightness(1.25);
    assert.match(memory.get("ada-appearance") ?? "", /"brightness":1.25/);
});

test("black, and the grey ada writes, are 'no colour'; anything else is a colour", () => {
    assert.equal(A.isUncoloured(0, 0, 0), true);
    assert.equal(A.isUncoloured(0.8, 0.8, 0.8), true);
    assert.equal(A.isUncoloured(Math.fround(0.8), Math.fround(0.8), Math.fround(0.8)), true, "as a file's 32-bit floats");
    assert.equal(A.isUncoloured(0.8, 0.8, 0.79), false);
    assert.equal(A.isUncoloured(1, 1, 1), false);
    assert.equal(A.isUncoloured(0, 0, 0.01), false);
});
