// Fading objects on the three.js-free viewport (`ViewportApi.setObjectFade`): the real scene and
// the real viewport interface over it, with a stand-in for the renderer that remembers what each
// instance was last told. A faded object is a hidden segment of its instance and a drawn one of a
// translucent instance of the same geometry -- still drawn, never picked -- and the user's own hiding
// is neither changed nor brought back.
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
globals.requestAnimationFrame = () => 1;
globals.cancelAnimationFrame = () => {};

const {useViewerHistoryStore} = await import("@/state/viewerHistoryStore");
const {InstanceFlag, SegmentFlag} = await import("@/viewport/adacpp/types");
const {setActiveViewport} = await import("@/viewport/api/viewportApi");
const {NativeScene} = await import("@/viewport/native/nativeScene");
const {createNativeViewportApi} = await import("@/viewport/native/nativeViewportApi");
type ViewportApi = import("@/viewport/api/viewportApi").ViewportApi;
type Scene = import("@/viewport/native/nativeScene").NativeScene;

const OUTLINE = 1000;
const GHOSTS = 5000;

/** A renderer that draws nothing: each load is `count` objects under a root, each one segment of
 *  one instance, with their feature edges as a second instance. */
function fakeRenderer(count: number) {
    const heap = new ArrayBuffer(1 << 20);
    const module = {_malloc: () => 1024, _free: () => {}, HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap)};
    const states = new Map<number, Uint32Array>();
    /** Instances made with `addInstance` (a ghost): the geometry each draws, and its last state. */
    const made = new Map<number, {geometry: number; opacity?: number; flags?: number}>();
    let instances = 0, extra = 0;
    const own = {
        loadGlb: () => {
            const instance = ++instances;
            const ids = Array.from({length: count}, (_, i) => `o${i}`);
            const objects = Int32Array.from(ids, (_, i) => i + 1);
            return {
                bounds: [0, 0, 0, count, 1, 1],
                extras: "",
                nodeObjects: false,
                geometries: [
                    {geometry: instance, instance, segmentObjects: objects},
                    {geometry: OUTLINE + instance, instance: OUTLINE + instance, featureEdges: true, segmentObjects: objects},
                ],
                objects: {
                    count: count + 1,
                    ids: ["root", ...ids],
                    names: ["root", ...ids],
                    parents: Int32Array.from([-1, ...ids.map(() => 0)]),
                    bounds: new Float32Array((count + 1) * 6).map((_, i) => (i % 6 < 3 ? 0 : 1)),
                    triangles: new Uint32Array(count + 1),
                    segmentOffsets: Uint32Array.from([0, 0, ...ids.map((_, i) => i + 1)]),
                    segments: Uint32Array.from(ids.flatMap((_, i) => [instance, i])),
                },
            };
        },
        setSegmentStates: (instance: number, p: number, n: number) => void states.set(instance, module.HEAPU32.slice(p >> 2, (p >> 2) + n * 2)),
        addInstance: (geometry: number) => {
            const id = GHOSTS + ++extra;
            made.set(id, {geometry});
            return id;
        },
        setInstance: (instance: number, _m: number, _r: number, _g: number, _b: number, a: number, flags: number) => {
            const m = made.get(instance);
            if (m) Object.assign(m, {opacity: a, flags});
        },
        removeInstance: (instance: number) => {
            made.delete(instance);
            states.delete(instance);
        },
        clear: () => {
            states.clear();
            made.clear();
        },
        cameraUpdate: () => false,
        cameraPose: () => ({eye: [-5, 5, 5], target: [0, 0, 0], up: [0, 0, 1], fov: 60, orthographic: false, orthoHeight: 0, azimuth: 0, elevation: 0, roll: 0, distance: 1}),
    };
    const renderer = new Proxy(own, {get: (target, name) => (target as Record<string | symbol, unknown>)[name] ?? (() => undefined)});
    return {module, renderer, states, made};
}

function makeScene(count = 4) {
    const fake = fakeRenderer(count);
    const input = {addEventListener: () => {}, removeEventListener: () => {}, setPointerCapture: () => {}};
    const container = {clientWidth: 800, clientHeight: 600};
    const canvas = {width: 0, height: 0, remove: () => {}, getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 600})};
    const device = {lost: new Promise(() => {}), destroy: () => {}};
    const Scene = NativeScene as unknown as new (...args: unknown[]) => Scene;
    const scene = new Scene(container, input, canvas, fake.module, fake.renderer, device);
    scene.load(new Uint8Array(4), "m1", {sourceName: "one.glb"});
    return {scene, fake};
}

let h: ReturnType<typeof makeScene>;
let api: ViewportApi;
let withdraw: () => void = () => {};
const hist = () => useViewerHistoryStore.getState();

beforeEach(() => {
    withdraw();
    h = makeScene();
    api = createNativeViewportApi(h.scene);
    withdraw = setActiveViewport(api);
    hist().reset();
});

/** What the renderer was last told of each object of `instance`: drawn ("own") or hidden. */
function picture(instance: number): string {
    const states = h.fake.states.get(instance);
    if (!states) return "";
    const out: string[] = [];
    for (let i = 0; i * 2 < states.length; i++) out.push(states[i * 2 + 1] & SegmentFlag.Hidden ? "hidden" : "own");
    return out.join(" ");
}

/** The ghosts the renderer holds: per instance made of a model's geometry (not a helper line's,
 *  which this renderer gives none), the geometry it draws. */
const ghosts = () => [...h.fake.made].filter(([, m]) => m.geometry !== undefined).map(([id, m]) => ({id, ...m}));
const fade = (keep: Record<string, string[]>, opacity: number) => ({keep: new Map(Object.entries(keep).map(([k, v]) => [k, new Set(v)])), opacity});

test("the rest of a model is faded: still drawn, by a see-through instance of its geometry that is never picked", () => {
    api.setObjectFade!(fade({"one.glb": ["o1"]}, 0.2));
    assert.equal(picture(1), "hidden own hidden hidden", "the instance draws only what is kept");
    assert.equal(picture(OUTLINE + 1), "hidden own hidden hidden", "the faded objects' edges go with them");
    const [ghost, ...more] = ghosts();
    assert.equal(more.length, 0, "one ghost, for the faces; edges get none");
    assert.equal(ghost.geometry, 1, "the model's own geometry: nothing is copied");
    assert.ok(Math.abs(ghost.opacity! - 0.2) < 1e-6);
    for (const flag of [InstanceFlag.Transparent, InstanceFlag.NoPick, InstanceFlag.DoubleSided]) assert.ok(ghost.flags! & flag);
    assert.equal(picture(ghost.id), "own hidden own own", "the ghost draws only the faded objects");

    // Not the user's hiding: nothing reads hidden, and no undo step was made.
    const shown = api.objectVisibility();
    assert.deepEqual(["o0", "o1", "o2"].map((id) => shown("m1", id)), ["shown", "shown", "shown"]);
    assert.equal(hist().past.length, 0);

    // Another opacity, and another focus: the same ghost, told again.
    api.setObjectFade!(fade({"one.glb": ["o2", "o3"]}, 0.5));
    assert.deepEqual(ghosts().map((g) => g.id), [ghost.id]);
    assert.ok(Math.abs(ghosts()[0].opacity! - 0.5) < 1e-6);
    assert.equal(picture(1), "hidden hidden own own");
    assert.equal(picture(ghost.id), "own own hidden hidden");

    // Taken away: every object as it was, the ghost gone.
    api.setObjectFade!(null);
    assert.equal(picture(1), "own own own own");
    assert.equal(picture(OUTLINE + 1), "own own own own");
    assert.deepEqual(ghosts(), []);
});

test("what the user hid stays hidden, under a fade and after it; opacity 0 draws the rest not at all", () => {
    api.hide([{model: "m1", id: "o2"}]);
    api.setObjectFade!(fade({"one.glb": ["o1", "o2"]}, 0.3));
    assert.equal(picture(1), "hidden own hidden hidden", "a hidden member is not brought back");
    assert.equal(picture(ghosts()[0].id), "own hidden hidden own", "nor drawn faded");

    api.setObjectFade!(fade({"one.glb": ["o1"]}, 0));
    assert.equal(picture(1), "hidden own hidden hidden");
    assert.deepEqual(ghosts(), [], "hiding the rest needs no ghost");

    api.setObjectFade!(null);
    assert.equal(picture(1), "own own hidden own");
    assert.equal(api.objectVisibility()("m1", "o2"), "hidden");
});

test("a fade over several models: an empty set fades a model whole, a model not named is left alone", () => {
    h.scene.load(new Uint8Array(4), "m2", {add: true, sourceName: "two.glb"});
    h.scene.load(new Uint8Array(4), "m3", {add: true, sourceName: "three.glb"});
    api.setObjectFade!(fade({"one.glb": ["o0"], "two.glb": []}, 0.2));
    assert.equal(picture(1), "own hidden hidden hidden");
    assert.equal(picture(2), "hidden hidden hidden hidden");
    assert.equal(picture(3), "own own own own");
    assert.deepEqual(ghosts().map((g) => g.geometry).sort(), [1, 2]);

    // A model taken off takes its ghost with it, before its geometry goes.
    assert.ok(h.scene.unloadSource("two.glb"));
    assert.deepEqual(ghosts().map((g) => g.geometry), [1]);

    // Clear (the viewport's half of `clearScene`): the fade goes with the models it named.
    api.clearModels();
    assert.deepEqual(ghosts(), []);
    h.scene.load(new Uint8Array(4), "m1", {sourceName: "one.glb"});
    assert.equal(picture(4), "", "the model loaded again is not faded");
    assert.deepEqual(ghosts(), []);
});

test("a load that replaces the scene forgets the fade: the renderer dropped its ghosts", () => {
    api.setObjectFade!(fade({"one.glb": ["o0"]}, 0.2));
    assert.equal(ghosts().length, 1);
    h.scene.load(new Uint8Array(4), "m1", {sourceName: "one.glb"});
    api.setSelection([{model: "m1", id: "o3"}]);
    assert.equal(picture(2), "own own own own");
    assert.deepEqual(ghosts(), []);
});
