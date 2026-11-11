// The three.js-free viewport adapting the shared rules (viewport/api): a model is placed by the scene
// frame, measured before it is loaded so the renderer applies the offset itself; the ground grid is the
// shared one at the origin; an empty scene is seen from the shared default view, with its clip range.
import assert from "node:assert/strict";
import {test} from "node:test";

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
globals.requestAnimationFrame = () => 1;
globals.cancelAnimationFrame = () => {};

const {NativeScene} = await import("@/viewport/native/nativeScene");
const {InstanceFlag, Topology} = await import("@/viewport/adacpp/types");
const {useOptionsStore} = await import("@/state/optionsStore");
const {DEFAULT_VIEW} = await import("@/viewport/api/cameraFrame");
const {GROUND_GRID, groundGridLines} = await import("@/viewport/api/groundGrid");
const {recentreTranslation} = await import("@/viewport/api/sceneFrame");
type Scene = import("@/viewport/native/nativeScene").NativeScene;

/** The box the GLB reports before it is loaded: far from the origin. */
const FILE_BOX = [400000, 6500000, 10, 400200, 6500040, 50];

function makeScene() {
    const heap = new ArrayBuffer(1 << 20);
    const loads: {offset?: number[]}[] = [];
    const lines: {count: number; flags: number}[] = [];
    const poses: number[][] = [];
    const clips: number[][] = [];
    const geometries = new Map<number, number>();
    let geometry = 0;
    let instance = 0;
    const module = {
        _malloc: () => 1024, _free: () => {},
        HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap),
        glbBounds: () => FILE_BOX,
    };
    const own = {
        loadGlb: (_p: number, _n: number, opts: {offset?: number[]}) => {
            loads.push(opts);
            const o = opts.offset ?? [0, 0, 0];
            const box = [0, 1, 2, 3, 4, 5].map((k) => FILE_BOX[k] + o[k % 3]);
            return {
                bounds: box, extras: "", adaExtData: "", featureEdges: 0, nodeObjects: false,
                geometries: [{geometry: ++geometry, instance: ++instance, topology: 0, vertexCount: 3, vertexColors: false, featureEdges: false, segmentObjects: Int32Array.from([0])}],
                objects: {
                    count: 1, ids: ["a"], names: ["a"], parents: Int32Array.from([-1]), bounds: Float32Array.from(box),
                    triangles: new Uint32Array(1), segmentOffsets: Uint32Array.from([0, 1]), segments: Uint32Array.from([instance, 0]),
                },
            };
        },
        addGeometry: (topology: number, _p: number, count: number) => {
            geometries.set(++geometry, topology === Topology.Lines ? count : -1);
            return geometry;
        },
        addInstance: (g: number) => {
            geometries.set(-(++instance), g);
            return instance;
        },
        setInstance: (inst: number, _m: number, _r: number, _g: number, _b: number, _a: number, flags: number) => {
            const g = geometries.get(-inst)!;
            lines.push({count: geometries.get(g)!, flags});
        },
        cameraPose: () => ({eye: [-5, 5, 5], target: [0, 0, 0], up: [0, 0, 1], fov: 60, orthographic: false, orthoHeight: 0, azimuth: 0, elevation: 0, roll: 0, distance: 1}),
        cameraSetPose: (...args: number[]) => void poses.push(args),
        cameraSetClipRange: (near: number, far: number) => void clips.push([near, far]),
        cameraUpdate: () => false,
    };
    const renderer = new Proxy(own, {get: (target, name) => (target as Record<string | symbol, unknown>)[name] ?? (() => undefined)});
    const input = {addEventListener: () => {}, removeEventListener: () => {}, setPointerCapture: () => {}};
    const container = {clientWidth: 800, clientHeight: 600};
    const canvas = {width: 800, height: 600, remove: () => {}, getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 600})};
    const device = {lost: new Promise(() => {}), destroy: () => {}};
    const Ctor = NativeScene as unknown as new (...args: unknown[]) => Scene;
    const scene = new Ctor(container, input, canvas, module, renderer, device);
    return {scene, loads, lines, poses, clips};
}

test("an empty scene is seen from the shared default view, with its clip range", () => {
    const {poses, clips} = makeScene();
    const {eye, target, fovDeg, near, far} = DEFAULT_VIEW;
    assert.deepEqual(poses[0].slice(0, 6), [...eye, ...target]);
    assert.equal(poses[0][9], fovDeg);
    assert.deepEqual(clips[0], [near, far]);
});

test("the shared ground grid is drawn at the origin, under the model", () => {
    const {lines} = makeScene();
    const {lines: grid, centre} = groundGridLines(true);
    const underlay = lines.filter((l) => l.flags & InstanceFlag.Underlay);
    assert.deepEqual(underlay.map((l) => l.count).sort((a, b) => a - b), [centre.length / 3, grid.length / 3].sort((a, b) => a - b));
    assert.equal(GROUND_GRID.size, 100);
});

test("a model far from the origin is recentred by the renderer, and the scene's offset says so", () => {
    useOptionsStore.setState({lockTranslation: false, autoFit: true});
    const {scene, loads} = makeScene();
    scene.load(new Uint8Array(8), "m1");
    const want = recentreTranslation(FILE_BOX as unknown as Parameters<typeof recentreTranslation>[0], true);
    assert.deepEqual(loads[0].offset, want);
    assert.deepEqual(scene.sceneOffset, want);
    const b = scene.modelBounds!;
    assert.ok(Math.abs((b[0] + b[3]) / 2) < 1e-6 && Math.abs((b[1] + b[4]) / 2) < 1e-6, "centred over the origin");
});

test("a model that joins is placed by the scene's offset; a locked translation leaves the file's", () => {
    useOptionsStore.setState({lockTranslation: false});
    const {scene, loads} = makeScene();
    scene.load(new Uint8Array(8), "m1");
    scene.load(new Uint8Array(8), "m2", {add: true});
    assert.deepEqual(loads[1].offset, loads[0].offset);

    useOptionsStore.setState({lockTranslation: true});
    const locked = makeScene();
    locked.scene.load(new Uint8Array(8), "m1");
    assert.deepEqual(locked.loads[0].offset, [0, 0, 0]);
    assert.deepEqual(locked.scene.sceneOffset, [0, 0, 0]);
    useOptionsStore.setState({lockTranslation: false});
});
