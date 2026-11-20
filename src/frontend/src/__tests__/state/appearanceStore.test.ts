// The appearance settings every renderer reads: their defaults (and how a version-1 user's stored
// settings meet them), and each renderer is handed the same two light numbers.
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

test("the defaults: half the viewer's old lights, results shaded, the background and grey it always had, white edges", () => {
    assert.equal(store().brightness, 0.5);
    assert.deepEqual(A.lightIntensities(), {ambient: Math.PI / 4, headlight: 0.7});
    // Brightness 1 is exactly the lights the viewer had before the settings.
    assert.deepEqual(A.lightIntensities({brightness: 1, ambientShare: A.APPEARANCE_DEFAULTS.ambientShare}), {ambient: Math.PI / 2, headlight: 1.4});
    assert.equal(store().background, "#393939");
    assert.deepEqual(A.defaultColorLinear(), [0.8, 0.8, 0.8]);
    // #e7e7e7 is what 0.8 linear looks like on screen.
    A.hexLinear(A.APPEARANCE_DEFAULTS.defaultColor).forEach((c) => assert.ok(Math.abs(c - 0.8) < 0.005, String(c)));
    assert.equal(store().shadeResults, true);
    assert.equal(store().edgeColor, "#ffffff");
    assert.equal(store().edgeBrightness, 1);
    assert.equal(A.appearanceIsDefault(), true);
    assert.equal(A.edgeLookIsDefault(), true);
});

test("version-1 settings: the old defaults become the new ones, a value the user chose is kept", () => {
    // Never touched brightness or shading (another setting was changed, so all were stored).
    assert.deepEqual(A.migrateAppearance({brightness: 1, shadeResults: false, background: "#102030"}, 1), {
        brightness: 0.5, shadeResults: true, background: "#102030",
    });
    // Chosen: kept.
    assert.deepEqual(A.migrateAppearance({brightness: 1.25, shadeResults: true}, 1), {brightness: 1.25, shadeResults: true});
    assert.deepEqual(A.migrateAppearance({brightness: 0.75, shadeResults: false}, 1), {brightness: 0.75, shadeResults: true});
    // This version's own values are never touched.
    assert.deepEqual(A.migrateAppearance({brightness: 1, shadeResults: false}, 2), {brightness: 1, shadeResults: false});
    assert.equal(A.migrateAppearance(null, 1), null);
});

test("version-1 settings in storage are read through the migration", async () => {
    memory.set("ada-appearance", JSON.stringify({state: {...A.APPEARANCE_DEFAULTS, brightness: 1, shadeResults: false, background: "#102030"}, version: 1}));
    await A.useAppearanceStore.persist.rehydrate();
    assert.equal(store().brightness, 0.5);
    assert.equal(store().shadeResults, true);
    assert.equal(store().background, "#102030");
    assert.match(memory.get("ada-appearance") ?? "", /"version":2/);
    memory.set("ada-appearance", JSON.stringify({state: {brightness: 1, shadeResults: false}, version: 2}));
    await A.useAppearanceStore.persist.rehydrate();
    assert.equal(store().brightness, 1, "chosen under version 2: kept");
    assert.equal(store().shadeResults, false);
});

test("the edge colour and brightness are refused when not values, and have their own reset", () => {
    store().setEdgeColor("#FF8800");
    store().setEdgeBrightness(0.4);
    store().setBrightness(1.5);
    assert.equal(store().edgeColor, "#ff8800");
    assert.equal(store().edgeBrightness, 0.4);
    store().setEdgeColor("orange");
    store().setEdgeBrightness(3);
    assert.equal(store().edgeColor, "#ff8800");
    assert.equal(store().edgeBrightness, A.EDGE_BRIGHTNESS_RANGE.max);
    assert.equal(A.edgeLookIsDefault(), false);
    store().resetEdges();
    assert.equal(A.edgeLookIsDefault(), true);
    assert.equal(store().brightness, 1.5, "the edges' reset leaves the rest");
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
    store().setShadeResults(false);
    store().setEdgeColor("#000000");
    store().reset();
    assert.equal(A.appearanceIsDefault(), true);
    assert.deepEqual(A.sanitizeAppearance({brightness: "2", background: "#12", ambientShare: 7, shadeResults: 1, edgeColor: "#fff", edgeBrightness: -1}), {
        ...A.APPEARANCE_DEFAULTS,
        ambientShare: 1,
        edgeBrightness: 0,
    });
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
