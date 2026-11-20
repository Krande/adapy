// A shaded FEA result keeps its legend's hue and saturation: the lighting only darkens the colour
// (resultShading.ts). Checked over every colour of every colour map, at every brightness setting.
import assert from "node:assert/strict";
import {test} from "node:test";

const memory = new Map<string, string>();
(globalThis as unknown as Record<string, unknown>).localStorage = {
    getItem: (k: string) => memory.get(k) ?? null,
    setItem: (k: string, v: string) => void memory.set(k, String(v)),
    removeItem: (k: string) => void memory.delete(k),
};

const {COLORMAP_NAMES, getColormap, linearColormap} = await import("@/utils/scene/fea/colormaps");
const {APPEARANCE_DEFAULTS, BRIGHTNESS_RANGE} = await import("@/state/appearanceStore");
const {resultExposure, resultShadeFactor, shadeResultColor} = await import("@/utils/scene/fea/resultShading");

const encode = (c: number) => (c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);

/** Hue (degrees), saturation and value of an sRGB colour, as a colour picker shows them. */
function hsv(c: ArrayLike<number>): [number, number, number] {
    const r = c[0], g = c[1], b = c[2];
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    let h = 0;
    if (d > 0) h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [(h * 60 + 360) % 360, mx > 0 ? d / mx : 0, mx];
}
const hueGap = (a: number, b: number) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b));

const lights = (brightness: number) => ({brightness, ambientShare: APPEARANCE_DEFAULTS.ambientShare});
const BRIGHTNESSES = [BRIGHTNESS_RANGE.min, APPEARANCE_DEFAULTS.brightness, 1, 1.5, BRIGHTNESS_RANGE.max];

test("the factor is one number in 0..1, largest square to the headlight, and never past the colour", () => {
    for (const b of BRIGHTNESSES) {
        let before = -1;
        for (const nDotL of [-1, 0, 0.25, 0.5, 0.75, 1]) {
            const k = resultShadeFactor(nDotL, lights(b));
            assert.ok(k >= 0 && k <= 1, `${b} ${nDotL}: ${k}`);
            assert.ok(k >= before, "more light, a brighter face");
            before = k;
        }
    }
    // The default lights (brightness 0.5): a face square to the light is at about half its colour's
    // light, and none is scaled down.
    assert.equal(resultExposure(lights(APPEARANCE_DEFAULTS.brightness)), 1);
    assert.ok(Math.abs(resultShadeFactor(1, lights(0.5)) - (Math.PI / 4 + 0.7) / Math.PI) < 1e-12);
    // Over-bright lights: the brightest face is the colour itself, the rest keep their shading.
    assert.ok(Math.abs(resultShadeFactor(1, lights(2)) - 1) < 1e-12);
    assert.ok(resultShadeFactor(0, lights(2)) < 0.6);
});

test("every colour-map colour keeps its hue and saturation when shaded: only its value drops", () => {
    const srgb = new Float32Array(3), lin = new Float32Array(3);
    let worstHue = 0, worstSat = 0, n = 0;
    for (const name of COLORMAP_NAMES) {
        const map = getColormap(name), linear = linearColormap(map);
        for (let i = 0; i <= 100; i++) {
            map(i / 100, srgb, 0);
            linear(i / 100, lin, 0);
            const [h0, s0, v0] = hsv(srgb);
            const sum0 = lin[0] + lin[1] + lin[2];
            for (const b of BRIGHTNESSES) {
                for (const nDotL of [1, 0.6, 0.2, 0]) {
                    const shadedLinear = shadeResultColor([lin[0], lin[1], lin[2]], nDotL, lights(b));
                    // In linear light the colour is the legend's times one number: its chromaticity is the legend's.
                    const sum = shadedLinear[0] + shadedLinear[1] + shadedLinear[2];
                    if (sum0 > 0) for (let c = 0; c < 3; c++) assert.ok(Math.abs(shadedLinear[c] / sum - lin[c] / sum0) < 1e-9);
                    const [h, s, v] = hsv(shadedLinear.map(encode));
                    assert.ok(v <= v0 + 1 / 255, "never brighter than the legend");
                    // Never paler: on screen a darkened colour's weak channels only fall further
                    // behind (sRGB's curve), so the saturation can rise a little, never drop.
                    assert.ok(s >= s0 - 0.01, `${name} ${i}: saturation ${s0.toFixed(3)} -> ${s.toFixed(3)}`);
                    // On screen, a face turned to the light: the colours a legend is read by
                    // (saturated, and lit enough to see).
                    if (nDotL < 0.6 || v < 0.2 || s0 < 0.25) continue;
                    worstHue = Math.max(worstHue, hueGap(h, h0));
                    worstSat = Math.max(worstSat, Math.abs(s - s0));
                    n++;
                }
            }
        }
    }
    assert.ok(n > 1000);
    // sRGB encodes each channel on its own curve, so a darkened colour's channel ratios move a little
    // on screen (most at the dimmest brightness); in linear light they are exactly the legend's.
    assert.ok(worstHue < 4, `worst hue change ${worstHue.toFixed(2)} degrees`);
    assert.ok(worstSat < 0.1, `worst saturation change ${worstSat.toFixed(3)}`);
});

test("what greyed the colours before: a highlight added to every channel, and channels clipped at 1", () => {
    // A pure green in linear light, square to the headlight.
    const green: [number, number, number] = [0, 1, 0];
    const [, s0] = hsv(green.map(encode));
    // The highlight three.js' matte standard material adds (F0 0.04, roughness 1) under the old
    // brightness 1, about 0.0045 of linear light on every channel, lifts the dark channels.
    const lit = 1.4 * (0.04 * 0.25) / Math.PI;
    const withHighlight = green.map((c) => c * 0.946 + lit).map(encode);
    assert.ok(s0 - hsv(withHighlight)[1] > 0.04, "a highlight greys it");
    // Over-bright lights (brightness 2) clip each channel at 1: the weak ones catch up, towards mint.
    const clipped = green.map((c) => Math.min(1, c * 1.89)).map(encode);
    const mid: [number, number, number] = [0.1, 1, 0.45]; // a jet green-cyan
    const midClipped = mid.map((c) => Math.min(1, c * 1.89)).map(encode);
    assert.ok(hsv(mid.map(encode))[1] - hsv(midClipped)[1] > 0.1, "clipping greys it");
    assert.ok(hsv(clipped)[1] <= s0);
    // Shaded as now: the same saturation, whatever the brightness.
    for (const b of [0.5, 1, 2]) {
        const now = shadeResultColor(mid, 1, lights(b)).map(encode);
        assert.ok(Math.abs(hsv(now)[1] - hsv(mid.map(encode))[1]) < 0.03, `brightness ${b}`);
    }
});
