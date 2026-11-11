// A result colour is the legend's colour on screen: the kernels hand over linear light, a painted
// result is drawn flat, and what the adacpp renderer keeps (8 bits of linear light per channel)
// comes back within a stated number of sRGB levels.
import assert from "node:assert/strict";
import {test} from "node:test";

const memory = new Map<string, string>();
(globalThis as unknown as Record<string, unknown>).localStorage = {
    getItem: (k: string) => memory.get(k) ?? null,
    setItem: (k: string, v: string) => void memory.set(k, String(v)),
    removeItem: (k: string) => void memory.delete(k),
};

const {COLORMAP_NAMES, getColormap, linearColormap, srgbToLinear} = await import("@/utils/scene/fea/colormaps");
const {useAppearanceStore} = await import("@/state/appearanceStore");
const {InstanceFlag} = await import("@/viewport/adacpp/types");
const {NativeResultLayer} = await import("@/viewport/native/nativeResultLayer");
type AdacppModule = import("@/viewport/adacpp/types").AdacppModule;
type WebRendererApi = import("@/viewport/adacpp/types").WebRendererApi;

const encode = (c: number) => (c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);

/** The sRGB levels (0..255) a colour comes back within after 8 bits of linear light: the adacpp
 *  renderer's vertex colours. Measured below; the browser tests allow this much and a little more. */
const LINEAR_8BIT_LEVELS = 7;

test("the table decodes sRGB as the formula does", () => {
    for (const c of [0, 0.01, 0.04045, 0.2, 0.5, 0.735, 1]) {
        const exact = c < 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        assert.ok(Math.abs(srgbToLinear(c) - exact) < 4e-4, `${c}: ${srgbToLinear(c)} vs ${exact}`);
    }
    assert.equal(srgbToLinear(-1), 0);
    assert.equal(srgbToLinear(2), 1);
});

test("a linear colour map, encoded for the screen, is the colour map: within half a level", () => {
    const srgb = new Float32Array(3), linear = new Float32Array(3);
    let worst = 0;
    for (const name of COLORMAP_NAMES) {
        const map = getColormap(name);
        assert.equal(linearColormap(map), linearColormap(map), "one function per map");
        for (let i = 0; i <= 200; i++) {
            map(i / 200, srgb, 0);
            linearColormap(map)(i / 200, linear, 0);
            for (let k = 0; k < 3; k++) worst = Math.max(worst, Math.abs(encode(linear[k]) - srgb[k]) * 255);
        }
    }
    assert.ok(worst < 0.5, `worst ${worst.toFixed(3)} levels`);
});

test("8 bits of linear light bring every colour-map colour back within the stated levels", () => {
    const linear = new Float32Array(3), srgb = new Float32Array(3);
    let worst = 0, sum = 0, n = 0;
    for (const name of COLORMAP_NAMES) {
        const map = getColormap(name);
        for (let i = 0; i <= 500; i++) {
            map(i / 500, srgb, 0);
            linearColormap(map)(i / 500, linear, 0);
            for (let k = 0; k < 3; k++) {
                const kept = Math.round(linear[k] * 255) / 255;
                const error = Math.abs(encode(kept) - srgb[k]) * 255;
                worst = Math.max(worst, error);
                sum += error;
                n++;
            }
        }
    }
    assert.ok(worst <= LINEAR_8BIT_LEVELS, `worst ${worst.toFixed(2)} levels`);
    assert.ok(sum / n < 1, `mean ${(sum / n).toFixed(2)} levels`);
});

function fakeRenderer() {
    const heap = new ArrayBuffer(1 << 16);
    let top = 8;
    const module = {
        HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap),
        _malloc: (bytes: number) => {
            const p = top;
            top += Math.ceil(bytes / 8) * 8;
            return p;
        },
        _free: () => {},
    } as unknown as AdacppModule;
    const seen = {flags: 0, sets: 0, colors: [] as number[]};
    const renderer = {
        addGeometry: () => 1,
        addInstance: () => 2,
        setInstance: (_i: number, _m: number, _r: number, _g: number, _b: number, _a: number, flags: number) => {
            seen.flags = flags;
            seen.sets++;
        },
        updateColors: (_g: number, p: number, n: number) => void (seen.colors = [...module.HEAPU32.subarray(p >> 2, (p >> 2) + n)]),
        updateMorph: () => {},
    } as unknown as WebRendererApi;
    return {module, renderer, seen};
}

const TRIANGLE = {positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), indices: new Uint32Array([0, 1, 2]), topology: "triangles" as const};

test("a painted result is drawn flat, an unpainted one lit; 'Shade FEA results' lights it at once", () => {
    useAppearanceStore.getState().reset();
    const {module, renderer, seen} = fakeRenderer();
    const layer = new NativeResultLayer(module, renderer, TRIANGLE, () => {});
    assert.equal(seen.flags & InstanceFlag.Unlit, 0, "unpainted: lit like a model");
    layer.setColors(new Float32Array([1, 0, 0, 0, 0.5, 0, 0, 0, 1]));
    assert.ok(seen.flags & InstanceFlag.Unlit, "painted: its colours are the legend's");
    assert.ok(seen.flags & InstanceFlag.VertexColors);
    // The colours are kept as given (linear light), 8 bits a channel.
    assert.deepEqual(seen.colors.map((c) => c & 0xffffff), [0x0000ff, 0x008000, 0xff0000]);

    layer.setColorsVisible(false);
    assert.equal(seen.flags & InstanceFlag.Unlit, 0, "colours off: the plain model, lit");
    layer.setColorsVisible(true);

    const sets = seen.sets;
    useAppearanceStore.getState().setShadeResults(true);
    layer.refreshLook();
    assert.equal(seen.sets, sets + 1, "one instance update, no geometry");
    assert.equal(seen.flags & InstanceFlag.Unlit, 0);
    useAppearanceStore.getState().setShadeResults(false);
    layer.refreshLook();
    assert.ok(seen.flags & InstanceFlag.Unlit);
});

test("lines and annotations are untouched by the setting", () => {
    useAppearanceStore.getState().reset();
    const {module, renderer, seen} = fakeRenderer();
    const lines = new NativeResultLayer(module, renderer, {positions: TRIANGLE.positions, indices: new Uint32Array([0, 1]), topology: "lines"}, () => {});
    const sets = seen.sets;
    lines.refreshLook();
    assert.equal(seen.sets, sets);
});
