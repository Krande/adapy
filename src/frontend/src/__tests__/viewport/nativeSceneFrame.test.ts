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

function makeScene(cameraUp: number[] = [0, 0, 1]) {
    const heap = new ArrayBuffer(1 << 20);
    const loads: {offset?: number[]}[] = [];
    const lines: {count: number; flags: number}[] = [];
    const poses: number[][] = [];
    const clips: number[][] = [];
    const geometries = new Map<number, number>();
    let geometry = 0;
    let instance = 0;
    // Mutable, so a test can prove the SECOND load's own box is ignored when the offset is reused
    // (as opposed to merely recomputing an identical offset from an identical box).
    let fileBox = FILE_BOX;
    const module = {
        _malloc: () => 1024, _free: () => {},
        HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap),
        glbBounds: () => fileBox,
    };
    const own = {
        loadGlb: (_p: number, _n: number, opts: {offset?: number[]}) => {
            loads.push(opts);
            const o = opts.offset ?? [0, 0, 0];
            const box = [0, 1, 2, 3, 4, 5].map((k) => fileBox[k] + o[k % 3]);
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
        cameraPose: () => ({eye: [-5, 5, 5], target: [0, 0, 0], up: cameraUp, fov: 60, orthographic: false, orthoHeight: 0, azimuth: 0, elevation: 0, roll: 0, distance: 1}),
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
    return {scene, loads, lines, poses, clips, setFileBox: (b: number[]) => void (fileBox = b)};
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

test("a replacing load with keepCamera reuses the scene's offset instead of recentring on its own box", () => {
    // A compile/recompile result: the model must not shift under the still camera that
    // `keepCamera` already keeps, so a REPLACE (no `add`) still needs the PRIOR offset reused when
    // told to keep the camera. The second load reports a DIFFERENT box than the first: recomputing
    // from it (as a plain replace would) gives a different offset, so reuse is the only way the
    // asserted equality can hold. This also pins down that place() cannot gate reuse on
    // `this.models.size`, which is already 0 by the time a replacing load gets there.
    useOptionsStore.setState({lockTranslation: false, autoFit: true});
    const {scene, loads, setFileBox} = makeScene();
    scene.load(new Uint8Array(8), "m1"); // first load: no offset yet, recentres as always
    const firstOffset = loads[0].offset;
    const recomputedFromSecondBox = recentreTranslation(
        [500000, 6600000, 20, 500100, 6600030, 60] as unknown as Parameters<typeof recentreTranslation>[0],
        true,
    );
    assert.notDeepEqual(recomputedFromSecondBox, firstOffset, "the two boxes must actually differ");
    setFileBox([500000, 6600000, 20, 500100, 6600030, 60]);
    scene.load(new Uint8Array(8), "m1", {keepCamera: true}); // a recompile replacing the same key
    assert.deepEqual(loads[1].offset, firstOffset, "the second load reuses the first's offset, not its own box");
    assert.deepEqual(scene.sceneOffset, firstOffset);
});

test("a replacing load keyed on translate (not keepCamera) reuses the scene's offset the same way", () => {
    // loadModel's own `translate` flag -- not `keepCamera` -- is the authority on reuse
    // once a caller gives one, so a plugin's `keepCamera` load (which gives neither) still falls
    // back to it (the next test), and loadModel's "replaceSource"/overlay loads (which give
    // `translate` but may leave `keepCamera` unset) behave the same on both viewports.
    useOptionsStore.setState({lockTranslation: false, autoFit: true});
    const {scene, loads, setFileBox} = makeScene();
    scene.load(new Uint8Array(8), "m1");
    const firstOffset = loads[0].offset;
    setFileBox([500000, 6600000, 20, 500100, 6600030, 60]);
    scene.load(new Uint8Array(8), "m1", {translate: true}); // no keepCamera at all
    assert.deepEqual(loads[1].offset, firstOffset, "translate alone is enough to reuse the offset");
    assert.deepEqual(scene.sceneOffset, firstOffset);
});

test("translate: false overrides a stray keepCamera: the offset is recomputed from the new box", () => {
    useOptionsStore.setState({lockTranslation: false, autoFit: true});
    const {scene, loads, setFileBox} = makeScene();
    scene.load(new Uint8Array(8), "m1");
    const secondBox = [500000, 6600000, 20, 500100, 6600030, 60];
    setFileBox(secondBox);
    scene.load(new Uint8Array(8), "m1", {keepCamera: true, translate: false});
    const want = recentreTranslation(secondBox as unknown as Parameters<typeof recentreTranslation>[0], true);
    assert.deepEqual(loads[1].offset, want, "translate: false takes over from keepCamera");
});

test("a load keyed on translate reuses the offset after the only other source was unloaded", () => {
    // The "replaceSource" fallback to the overlay path (loadModel.ts) unloads this same-named
    // source first; when it was the only thing on screen, `this.models` is empty by the time this
    // load gets here, so `hadModels` alone would miss the reuse and the model would jump to a
    // freshly recentred offset under a still camera. `offsetValid` (set once a model has ever been
    // placed, left alone by `unloadSource`) covers it.
    useOptionsStore.setState({lockTranslation: false, autoFit: true});
    const {scene, loads, setFileBox} = makeScene();
    scene.load(new Uint8Array(8), "m1", {sourceName: "A", translate: true});
    const firstOffset = loads[0].offset;
    assert.equal(scene.unloadSource("A"), true);
    const secondBox = [500000, 6600000, 20, 500100, 6600030, 60];
    setFileBox(secondBox);
    scene.load(new Uint8Array(8), "m1", {sourceName: "A", translate: true});
    assert.deepEqual(loads[1].offset, firstOffset, "the offset is reused, not recomputed from the new box");
    assert.deepEqual(scene.sceneOffset, firstOffset);
});

test("a first-ever load with keepCamera still recentres: there is no prior offset to reuse", () => {
    useOptionsStore.setState({lockTranslation: false, autoFit: true});
    const {scene, loads} = makeScene();
    scene.load(new Uint8Array(8), "m1", {keepCamera: true});
    const want = recentreTranslation(FILE_BOX as unknown as Parameters<typeof recentreTranslation>[0], true);
    assert.deepEqual(loads[0].offset, want);
});

test("a double-click's new orbit pivot keeps the world's up, whatever way the camera's own up leans", () => {
    // After an orbit drag the camera's own up leans with its pitch (here: looking down at 45 degrees).
    const s = Math.SQRT1_2;
    const {scene, poses} = makeScene([s / Math.SQRT2, -s / Math.SQRT2, s]);
    const before = poses.length;
    scene.setOrbitPivot([2, 1, 0]);
    assert.equal(poses.length, before + 1);
    const pose = poses[poses.length - 1];
    assert.deepEqual(pose.slice(0, 3), [-5, 5, 5], "the camera stays where it is");
    assert.deepEqual(pose.slice(3, 6), [2, 1, 0], "and looks at the new pivot");
    assert.deepEqual(pose.slice(6, 9), [0, 0, 1], "against the world's up: no roll, the orbit axis stays upright");
});

test("a replacing load frames the model with auto-fit on, and keeps the camera when told to", () => {
    useOptionsStore.setState({lockTranslation: false, autoFit: true});
    const framed = makeScene();
    const before = framed.poses.length;
    framed.scene.load(new Uint8Array(8), "m1");
    assert.ok(framed.poses.length > before, "auto-fit moves the camera");

    const kept = makeScene();
    const start = kept.poses.length;
    const clipsStart = kept.clips.length;
    kept.scene.load(new Uint8Array(8), "m1", {keepCamera: true});
    assert.equal(kept.poses.length, start, "the camera stays");
    assert.ok(kept.clips.length > clipsStart, "the clip range still follows the model");
});
