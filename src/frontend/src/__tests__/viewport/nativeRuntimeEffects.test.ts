// Effects applied to the three.js-free viewport from outside a model's file (`NativeScene.applyEffects`, what
// `ctx.effects` reaches): lights and emitters moved into the renderer's world, emissive ramps on the segments
// of the named model's objects, held across loads, drawn on the caller's clock. The real scene, with a
// stand-in for the renderer that records what it is told.
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

const {NativeScene} = await import("@/viewport/native/nativeScene");
type Scene = import("@/viewport/native/nativeScene").NativeScene;

interface Calls {
    applied: {json: Record<string, unknown>; look: boolean}[];
    removedSets: number[];
    ramps: {id: number; desc: Record<string, unknown>}[];
    removedRamps: number[];
    segmentEmissive: {instance: number; segments: number[]; ramp: number}[];
    setTime: number[];
    clears: number;
}

/**
 * A model of three objects: a root "o0" (instance 1, segment 0), its child "o1" (instance 1, segment 1) and
 * "o2" under "o1" (instance 2, segment 0). Each load gets new instance numbers.
 */
function fakeRenderer() {
    const heap = new ArrayBuffer(1 << 16);
    let next = 1024;
    const module = {_malloc: (n: number) => ((next += n + 16), next - n - 16), _free: () => {}, HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap)};
    const calls: Calls = {applied: [], removedSets: [], ramps: [], removedRamps: [], segmentEmissive: [], setTime: [], clears: 0};
    let instances = 0;
    let sets = 0;
    let ramps = 0;
    const own = {
        loadGlb: () => {
            const a = ++instances;
            const b = ++instances;
            return {
                bounds: [0, 0, 0, 1, 1, 1],
                extras: "",
                nodeObjects: false,
                geometries: [
                    {geometry: a, instance: a, segmentObjects: Int32Array.from([0, 1])},
                    {geometry: b, instance: b, segmentObjects: Int32Array.from([2])},
                ],
                objects: {
                    count: 3,
                    ids: ["o0", "o1", "o2"],
                    names: ["o0", "o1", "o2"],
                    parents: Int32Array.from([-1, 0, 1]),
                    bounds: Float32Array.from([0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1]),
                    triangles: new Uint32Array(3),
                    segmentOffsets: Uint32Array.from([0, 1, 2, 3]),
                    segments: Uint32Array.from([a, 0, a, 1, b, 0]),
                },
            };
        },
        clear: () => void calls.clears++,
        setTime: (t: number) => void calls.setTime.push(t),
        animating: () => false,
        applyEffects: (json: string, look: boolean) => {
            calls.applied.push({json: JSON.parse(json), look});
            return {set: ++sets, lights: [], emitters: [], ramps: [], unresolved: [], hasPost: false, hasEnvironment: false};
        },
        removeEffects: (set: number) => void calls.removedSets.push(set),
        addEmissiveRamp: () => ++ramps,
        setEmissiveRamp: (id: number, desc: Record<string, unknown>) => void calls.ramps.push({id, desc}),
        removeEmissiveRamp: (id: number) => void calls.removedRamps.push(id),
        setSegmentEmissive: (instance: number, p: number, n: number, ramp: number) =>
            void calls.segmentEmissive.push({instance, segments: Array.from(new Uint32Array(heap, p, n)), ramp}),
        cameraUpdate: () => false,
        cameraPose: () => ({eye: [-5, 5, 5], target: [0, 0, 0], up: [0, 0, 1], fov: 60, orthographic: false, orthoHeight: 0, azimuth: 0, elevation: 0, roll: 0, distance: 1}),
    };
    const renderer = new Proxy(own, {get: (target, name) => (target as Record<string | symbol, unknown>)[name] ?? (() => undefined)});
    return {module, renderer, calls};
}

function makeScene() {
    const fake = fakeRenderer();
    const input = {addEventListener: () => {}, removeEventListener: () => {}, setPointerCapture: () => {}};
    const container = {clientWidth: 800, clientHeight: 600};
    const canvas = {width: 0, height: 0, remove: () => {}, getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 600})};
    const device = {lost: new Promise(() => {}), destroy: () => {}};
    const S = NativeScene as unknown as new (...args: unknown[]) => Scene;
    return {scene: new S(container, input, canvas, fake.module, fake.renderer, device), fake};
}

function runFrame(): boolean {
    const f = frame;
    frame = null;
    f?.(0);
    return frame !== null;
}

beforeEach(() => {
    frame = null;
});

const SPEC = {
    version: 1,
    lights: [{position: [1, 2, 3], intensity: 2, start: 1, end: 2}],
    emitters: [{preset: "sparks", origin: [0, 0, 0], velocity: [0.01, 0, 0], start: 1, end: 2}],
    emissive: [{targets: ["o1", "nope"], hotIntensity: 7, start: 1.5, duration: 6}],
};

test("lights and emitters go in one set, ramps on the segments of the targets and everything under them", () => {
    const {scene, fake} = makeScene();
    scene.load(new Uint8Array(4), "m1", {sourceName: "run.glb"});
    const held = scene.applyEffects(SPEC, {model: "run.glb"})!;
    assert.ok(held);
    assert.equal(fake.calls.applied.length, 1);
    const {json, look} = fake.calls.applied[0];
    assert.equal(look, false, "the look stays the viewer's");
    assert.deepEqual(Object.keys(json).sort(), ["emitters", "lights", "version"], "no ramps in the set: they are put on the segments here");
    // "o1" and "o2" under it: segment 1 of the first instance and segment 0 of the second.
    assert.deepEqual(fake.calls.ramps.map((r) => r.desc), [{hotIntensity: 7, start: 1.5, duration: 6}]);
    assert.deepEqual(
        fake.calls.segmentEmissive.map((s) => [s.instance, s.segments, s.ramp]),
        [[1, [1], 1], [2, [0], 1]],
    );
    assert.deepEqual(held.unresolved(), ["nope"]);
    assert.ok(scene.hasEffects);

    held.remove();
    assert.deepEqual(fake.calls.removedSets, [1]);
    assert.deepEqual(fake.calls.removedRamps, [1]);
    // The segments glow as their instance does again.
    assert.deepEqual(fake.calls.segmentEmissive.slice(2).map((s) => s.ramp), [-1, -1]);
    assert.ok(!scene.hasEffects);
});

test("applied with a model that is not on screen: nothing until it is, and on it again when it is loaded again", () => {
    const {scene, fake} = makeScene();
    const held = scene.applyEffects(SPEC, {model: "run.glb"})!;
    assert.equal(fake.calls.applied.length, 0);
    assert.ok(!scene.hasEffects);
    scene.load(new Uint8Array(4), "m1", {sourceName: "run.glb"});
    assert.equal(fake.calls.applied.length, 1, "put on with the model");
    // A replacing load empties the renderer: what was held is gone with it and is not removed (its ids may be
    // another's now); it is put on the new instances.
    const removedBefore = fake.calls.removedSets.length;
    scene.load(new Uint8Array(4), "m2", {sourceName: "run.glb"});
    assert.equal(fake.calls.removedSets.length, removedBefore);
    assert.equal(fake.calls.applied.length, 2);
    assert.deepEqual(fake.calls.segmentEmissive.at(-1)!.instance, 4, "on the model loaded again");
    // The model leaves the scene: its effects with it.
    scene.clearModels();
    assert.ok(!scene.hasEffects);
    held.remove();
});

test("an update replaces what was shown; the caller's clock draws them, and a moved clock draws again", () => {
    const {scene, fake} = makeScene();
    scene.load(new Uint8Array(4), "m1", {sourceName: "run.glb"});
    let now = 12.5;
    const listeners: (() => void)[] = [];
    const clock = {time: () => now, subscribe: (l: () => void) => (listeners.push(l), () => {})};
    const held = scene.applyEffects({version: 1, lights: SPEC.lights}, {model: "run.glb", clock})!;
    held.update({version: 1, emitters: SPEC.emitters});
    assert.deepEqual(fake.calls.removedSets, [1]);
    assert.deepEqual(Object.keys(fake.calls.applied.at(-1)!.json).sort(), ["emitters", "version"]);
    scene.invalidate();
    runFrame();
    assert.equal(fake.calls.setTime.at(-1), 12.5);
    // A seek while paused: the scene is told to draw the moment.
    let invalidated = 0;
    const own = scene.invalidate.bind(scene);
    scene.invalidate = () => {
        invalidated++;
        own();
    };
    now = 3;
    listeners.forEach((l) => l());
    assert.equal(invalidated, 1);
    runFrame();
    assert.equal(fake.calls.setTime.at(-1), 3);
    held.remove();
    held.remove();
    assert.deepEqual(fake.calls.removedSets, [1, 2], "removed once");
});

test("the viewport adapter offers the effects of the native scene", async () => {
    const {createNativeViewportApi} = await import("@/viewport/native/nativeViewportApi");
    const {scene} = makeScene();
    const api = createNativeViewportApi(scene);
    assert.equal(api.drawsEffects?.(), true);
    scene.load(new Uint8Array(4), "m1", {sourceName: "run.glb"});
    const held = api.applyEffects?.(SPEC, {model: "run.glb"});
    assert.ok(held);
    held!.remove();
});
