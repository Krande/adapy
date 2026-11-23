// Visual effects on the three.js-free viewport: the look settings (tone mapping, bloom, environment)
// handed to the adacpp renderer, a model's "ada_effects" asked for at load and released with it, and
// the effects' clock. The real scene, with a stand-in for the renderer that records what it is told.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

const memory = new Map<string, string>();
const storage = {
    getItem: (k: string) => memory.get(k) ?? null,
    setItem: (k: string, v: string) => void memory.set(k, String(v)),
    removeItem: (k: string) => void memory.delete(k),
};
const globals = globalThis as unknown as Record<string, unknown>;
globals.sessionStorage = storage;
globals.localStorage = storage;
globals.ResizeObserver = class {
    observe() {}
    disconnect() {}
};
let frame: ((t: number) => void) | null = null;
globals.requestAnimationFrame = (cb: (t: number) => void) => {
    frame = cb;
    return 1;
};
globals.cancelAnimationFrame = () => {};

const {APPEARANCE_DEFAULTS, nativeLook, sanitizeAppearance, useAppearanceStore} = await import("@/state/appearanceStore");
const {NativeScene} = await import("@/viewport/native/nativeScene");
type Scene = import("@/viewport/native/nativeScene").NativeScene;

interface Calls {
    setPost: unknown[];
    setEnvironment: unknown[];
    setTime: number[];
    removeEffects: number[];
    loadOptions: Record<string, unknown>[];
}

function fakeRenderer(effects: {lights: number[]; emitters: number[]; ramps: number[]} | null, animating = true) {
    const heap = new ArrayBuffer(1 << 16);
    const module = {_malloc: () => 1024, _free: () => {}, HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap)};
    const calls: Calls = {setPost: [], setEnvironment: [], setTime: [], removeEffects: [], loadOptions: []};
    let instances = 0;
    const own = {
        loadGlb: (_p: number, _n: number, options: Record<string, unknown>) => {
            calls.loadOptions.push(options);
            const instance = ++instances;
            return {
                bounds: [0, 0, 0, 1, 1, 1],
                extras: "",
                nodeObjects: false,
                geometries: [{geometry: instance, instance, segmentObjects: Int32Array.from([0])}],
                objects: {
                    count: 1,
                    ids: ["o0"],
                    names: ["o0"],
                    parents: Int32Array.from([-1]),
                    bounds: Float32Array.from([0, 0, 0, 1, 1, 1]),
                    triangles: new Uint32Array(1),
                    segmentOffsets: Uint32Array.from([0, 1]),
                    segments: Uint32Array.from([instance, 0]),
                },
                effects: options.effects && effects ? {set: instance, unresolved: [], hasPost: false, hasEnvironment: false, ...effects} : undefined,
            };
        },
        setPost: (s: unknown) => void calls.setPost.push(s),
        setEnvironment: (s: unknown) => void calls.setEnvironment.push(s),
        setTime: (t: number) => void calls.setTime.push(t),
        animating: () => animating,
        removeEffects: (set: number) => void calls.removeEffects.push(set),
        cameraUpdate: () => false,
        cameraPose: () => ({eye: [-5, 5, 5], target: [0, 0, 0], up: [0, 0, 1], fov: 60, orthographic: false, orthoHeight: 0, azimuth: 0, elevation: 0, roll: 0, distance: 1}),
    };
    const renderer = new Proxy(own, {get: (target, name) => (target as Record<string | symbol, unknown>)[name] ?? (() => undefined)});
    return {module, renderer, calls};
}

function makeScene(effects: {lights: number[]; emitters: number[]; ramps: number[]} | null, animating = true) {
    const fake = fakeRenderer(effects, animating);
    const input = {addEventListener: () => {}, removeEventListener: () => {}, setPointerCapture: () => {}};
    const container = {clientWidth: 800, clientHeight: 600};
    const canvas = {width: 0, height: 0, remove: () => {}, getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 600})};
    const device = {lost: new Promise(() => {}), destroy: () => {}};
    const S = NativeScene as unknown as new (...args: unknown[]) => Scene;
    const scene = new S(container, input, canvas, fake.module, fake.renderer, device);
    return {scene, fake};
}

/** Runs the frame the scene asked for, if any; whether it asked for another. */
function runFrame(): boolean {
    const f = frame;
    frame = null;
    f?.(0);
    return frame !== null;
}

beforeEach(() => {
    useAppearanceStore.getState().reset();
    frame = null;
});

test("the look settings are off by default and sanitised", () => {
    const look = nativeLook(APPEARANCE_DEFAULTS);
    assert.deepEqual(look.post, {hdr: false, toneMapping: "none", exposure: 1, bloom: false});
    assert.deepEqual(look.environment, {enabled: false, intensity: 1});
    const s = sanitizeAppearance({toneMapping: "filmic", exposure: 99, bloom: "yes", environment: true, environmentIntensity: -2});
    assert.equal(s.toneMapping, "off");
    assert.equal(s.exposure, 4);
    assert.equal(s.bloom, false);
    assert.equal(s.environment, true);
    assert.equal(s.environmentIntensity, 0);
    // Bloom alone draws in HDR, without a tone mapping of its own.
    assert.deepEqual(nativeLook({...APPEARANCE_DEFAULTS, bloom: true, exposure: 2}).post, {hdr: true, toneMapping: "none", exposure: 2, bloom: true});
});

test("the native scene hands the look to the renderer, and again when it changes", () => {
    const {fake} = makeScene(null);
    assert.deepEqual(fake.calls.setPost.at(-1), {hdr: false, toneMapping: "none", exposure: 1, bloom: false});
    assert.deepEqual(fake.calls.setEnvironment.at(-1), {enabled: false, intensity: 1});
    const posts = fake.calls.setPost.length;
    useAppearanceStore.getState().setToneMapping("aces");
    useAppearanceStore.getState().setEnvironment(true);
    assert.ok(fake.calls.setPost.length > posts);
    assert.deepEqual(fake.calls.setPost.at(-1), {hdr: true, toneMapping: "aces", exposure: 1, bloom: false});
    assert.deepEqual(fake.calls.setEnvironment.at(-1), {enabled: true, intensity: 1});
    // A change elsewhere does not touch it.
    const before = fake.calls.setPost.length;
    useAppearanceStore.getState().setBrightness(1.2);
    assert.equal(fake.calls.setPost.length, before);
});

test("a model's effects are asked for, run on the clock while they move, and go with the model", () => {
    const {scene, fake} = makeScene({lights: [0], emitters: [0, 1], ramps: []});
    scene.load(new Uint8Array(4), "m1", {sourceName: "fx.glb"});
    assert.equal(fake.calls.loadOptions[0].effects, true);
    assert.ok(scene.hasEffects);
    scene.invalidate();
    assert.equal(runFrame(), true, "moving effects keep the loop drawing");
    assert.equal(fake.calls.setTime.length, 1);
    assert.ok(fake.calls.setTime[0] >= 0 && fake.calls.setTime[0] < 5);
    scene.clearModels();
    assert.deepEqual(fake.calls.removeEffects, [1]);
    assert.ok(!scene.hasEffects);
});

test("a model without effects costs nothing per frame", () => {
    const {scene, fake} = makeScene({lights: [], emitters: [], ramps: []});
    scene.load(new Uint8Array(4), "m1", {sourceName: "plain.glb"});
    assert.ok(!scene.hasEffects);
    assert.deepEqual(fake.calls.removeEffects, [1], "the empty set is released at once");
    scene.invalidate();
    runFrame();
    assert.equal(fake.calls.setTime.length, 0);
});

test("still effects do not keep the loop running", () => {
    const {scene, fake} = makeScene({lights: [0], emitters: [], ramps: []}, false);
    scene.load(new Uint8Array(4), "m1", {sourceName: "still.glb"});
    scene.invalidate();
    runFrame();
    assert.equal(fake.calls.setTime.length, 1);
});
