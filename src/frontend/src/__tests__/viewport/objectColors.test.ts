// Recolouring objects and the appearance settings on the three.js-free viewport: the real scene and
// the real viewport interface over it, with a stand-in for the renderer that remembers what each
// segment was last told. A colour is a segment's colour: no geometry, material or draw call.
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

const {useAppearanceStore} = await import("@/state/appearanceStore");
const {useViewerHistoryStore} = await import("@/state/viewerHistoryStore");
const {redoViewerAction, undoViewerAction} = await import("@/utils/scene/viewerHistory");
const {SegmentFlag} = await import("@/viewport/adacpp/types");
const {setActiveViewport} = await import("@/viewport/api/viewportApi");
const {NativeScene} = await import("@/viewport/native/nativeScene");
const {createNativeViewportApi} = await import("@/viewport/native/nativeViewportApi");
type ViewportApi = import("@/viewport/api/viewportApi").ViewportApi;
type Scene = import("@/viewport/native/nativeScene").NativeScene;

const OUTLINE = 1000;
const RED = 0xff0000ff; // linear RGBA8 as the renderer takes a segment's colour: r in the low byte
const SELECTED = 0xffff0000;

/** A renderer that draws nothing: `count` objects under a root, each one segment of one instance,
 *  with their feature edges as a second instance. */
function fakeRenderer(count: number) {
    const heap = new ArrayBuffer(1 << 20);
    const module = {_malloc: () => 1024, _free: () => {}, HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap)};
    const states = new Map<number, Uint32Array>();
    const calls: Record<string, number[][]> = {setLighting: [], setBackground: []};
    let stateWrites = 0, instances = 0, geometries = 0, helpers = 0;
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
        setSegmentStates: (instance: number, p: number, n: number) => {
            stateWrites++;
            states.set(instance, module.HEAPU32.slice(p >> 2, (p >> 2) + n * 2));
        },
        addGeometry: () => ++geometries + 5000,
        addInstance: () => ++helpers + 5000,
        setLighting: (a: number, h: number) => void calls.setLighting.push([a, h]),
        setBackground: (r: number, g: number, b: number) => void calls.setBackground.push([r, g, b]),
        clear: () => states.clear(),
        cameraUpdate: () => false,
        cameraPose: () => ({eye: [-5, 5, 5], target: [0, 0, 0], up: [0, 0, 1], fov: 60, orthographic: false, orthoHeight: 0, azimuth: 0, elevation: 0, roll: 0, distance: 1}),
    };
    const renderer = new Proxy(own, {get: (target, name) => (target as Record<string | symbol, unknown>)[name] ?? (() => undefined)});
    return {module, renderer, states, calls, writes: () => stateWrites, geometries: () => geometries};
}

function makeScene(count = 4, bytes: Uint8Array = new Uint8Array(4)) {
    const fake = fakeRenderer(count);
    const input = {addEventListener: () => {}, removeEventListener: () => {}, setPointerCapture: () => {}};
    const container = {clientWidth: 800, clientHeight: 600};
    const canvas = {width: 0, height: 0, remove: () => {}, getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 600})};
    const device = {lost: new Promise(() => {}), destroy: () => {}};
    const Scene = NativeScene as unknown as new (...args: unknown[]) => Scene;
    const scene = new Scene(container, input, canvas, fake.module, fake.renderer, device);
    scene.load(bytes, "m1", {sourceName: "one.glb"});
    return {scene, fake};
}

/** A GLB that is only its JSON: enough for the scene to learn which objects have no colour. */
function glbJson(doc: unknown): Uint8Array {
    let text = JSON.stringify(doc);
    while (text.length % 4) text += " ";
    const json = new TextEncoder().encode(text);
    const bytes = new Uint8Array(20 + json.length);
    const view = new DataView(bytes.buffer);
    view.setUint32(0, 0x46546c67, true);
    view.setUint32(4, 2, true);
    view.setUint32(8, bytes.length, true);
    view.setUint32(12, json.length, true);
    view.setUint32(16, 0x4e4f534a, true);
    bytes.set(json, 20);
    return bytes;
}

const hist = () => useViewerHistoryStore.getState();
const ref = (i: number, model = "m1") => ({model, id: `o${i}`});
let h: ReturnType<typeof makeScene>;
let api: ViewportApi;
let withdraw: () => void = () => {};

function use(made: ReturnType<typeof makeScene>): void {
    withdraw();
    h = made;
    api = createNativeViewportApi(h.scene);
    withdraw = setActiveViewport(api);
    hist().reset();
}

/** What the renderer was last told of each object of instance 1. */
function picture(instance = 1): string {
    const states = h.fake.states.get(instance) ?? new Uint32Array(0);
    const out: string[] = [];
    for (let i = 0; i * 2 < states.length; i++) {
        const color = states[i * 2], flags = states[i * 2 + 1];
        out.push(flags & SegmentFlag.Hidden ? "hidden" : color === SELECTED ? "selected" : color ? `#${(color & 0xffffff).toString(16).padStart(6, "0")}` : "own");
    }
    return out.join(" ");
}

beforeEach(() => {
    useAppearanceStore.getState().reset();
    use(makeScene());
});

test("a colour for some objects, for all, and back: each one undo step", () => {
    assert.equal(api.setObjectColors([ref(1), ref(2)], "#ff0000"), 2);
    assert.equal(picture(), "own #0000ff #0000ff own");
    assert.equal(api.objectColorCount(), 2);
    // Linear light: #808080 is 0.216, kept in 8 bits.
    assert.equal(api.setObjectColors(null, "#808080"), 4);
    assert.equal(picture(), "#373737 #373737 #373737 #373737");
    assert.equal(api.setObjectColors([ref(0)], null), 1);
    assert.equal(picture(), "own #373737 #373737 #373737");
    assert.equal(api.setObjectColors(null, null), 3);
    assert.equal(picture(), "own own own own");
    assert.deepEqual(hist().past.map((e) => e.label), ["set colour of objects", "set colour of objects", "reset colours of objects", "reset colours of objects"]);

    undoViewerAction();
    assert.equal(picture(), "own #373737 #373737 #373737");
    undoViewerAction();
    undoViewerAction();
    assert.equal(picture(), "own #0000ff #0000ff own");
    undoViewerAction();
    assert.equal(picture(), "own own own own");
    assert.equal(api.objectColorCount(), 0);
    redoViewerAction();
    redoViewerAction();
    assert.equal(picture(), "#373737 #373737 #373737 #373737");
    assert.equal(hist().future.length, 2);
});

test("a call that changes nothing is no step; a colour that is not one is refused", () => {
    assert.equal(api.setObjectColors([{model: "m1", id: "nope"}, {model: "other", id: "o1"}], "#ff0000"), 0);
    assert.equal(api.setObjectColors([ref(1)], "red"), 0);
    assert.equal(api.setObjectColors(null, null), 0);
    api.setObjectColors([ref(1)], "#ff0000");
    assert.equal(api.setObjectColors([ref(1)], "#ff0000"), 0, "the colour it has");
    assert.equal(hist().past.length, 1);
    // A level of the hierarchy draws nothing itself: it has no colour to change.
    assert.equal(api.setObjectColors([{model: "m1", id: "root"}], "#00ff00"), 0);
});

test("hidden wins, then the selection, then the colour; the edges keep their own", () => {
    api.setObjectColors([ref(0), ref(1), ref(2)], "#ff0000");
    api.setSelection([ref(1)]);
    api.hide([ref(2)]);
    assert.equal(picture(), "#0000ff #0000ff hidden own", "hiding cleared the selection");
    api.setSelection([ref(1)]);
    assert.equal(picture(), "#0000ff selected hidden own");
    // The selection's colour is matte; a given colour is lit by the object's own material.
    const states = h.fake.states.get(1)!;
    assert.equal(states[3], SegmentFlag.Matte);
    assert.equal(states[1], 0);
    assert.equal(picture(OUTLINE + 1), "own own hidden own");
    api.clearSelection();
    api.showAll();
    assert.equal(picture(), "#0000ff #0000ff #0000ff own", "show and deselect give the colours back");
    api.isolate([ref(0)]);
    api.showAll();
    assert.equal(picture(), "#0000ff #0000ff #0000ff own");
});

test("the elements of an FEA result are not recoloured: their colours are the result's", () => {
    h.scene.createResultLayer({
        positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), indices: new Uint32Array([0, 1, 2]), topology: "triangles",
        objects: {model: "result", ids: ["7"], segments: new Uint32Array([0, 3]), objectOf: new Int32Array([0])},
    });
    assert.equal(api.setObjectColors([{model: "result", id: "7"}], "#ff0000"), 0);
    assert.equal(api.setObjectColors(null, "#ff0000"), 4, "every object of the model, none of the result");
    assert.equal(api.objectColorCount(), 4);
});

test("colours go with their model, and undo finds a model loaded again by its source", () => {
    api.setObjectColors([ref(3)], "#ff0000");
    const before = api.captureObjectColors();
    assert.equal(api.captureObjectColors(), before, "the same state until a colour changes");
    assert.equal(before.count, 1);
    h.scene.load(new Uint8Array(4), "m2", {sourceName: "one.glb"});
    assert.equal(api.objectColorCount(), 0);
    api.restoreObjectColors(before);
    assert.equal(picture(2), "own own own #0000ff");
    api.restoreObjectColors({count: 9});
    assert.equal(api.objectColorCount(), 1, "a state of another viewport is ignored");
});

test("recolouring 10,000 objects is one pass over the segments: one write per instance, nothing made", () => {
    use(makeScene(10_000));
    const refs = {
        // One ref, reused: as a caller naming thousands of objects may hand them over.
        *[Symbol.iterator]() {
            const r = {model: "m1", id: ""};
            for (let i = 0; i < 10_000; i++) {
                r.id = `o${i}`;
                yield r;
            }
        },
    };
    api.setObjectColors([ref(0)], "#111111"); // the model's id index is made once
    const writes = h.fake.writes(), made = h.fake.geometries();
    const t0 = performance.now();
    assert.equal(api.setObjectColors(refs, "#3366cc"), 10_000);
    const recolour = performance.now() - t0;
    assert.equal(h.fake.writes() - writes, 2, "the faces and their edges: one state write each");
    assert.equal(h.fake.geometries(), made, "no geometry");
    const t1 = performance.now();
    assert.equal(api.setObjectColors(null, null), 10_000);
    const reset = performance.now() - t1;
    console.log(`three.js-free: recolour 10k objects ${recolour.toFixed(2)} ms, reset ${reset.toFixed(2)} ms`);
    assert.ok(recolour < 150 && reset < 150, `recolour ${recolour.toFixed(1)} ms, reset ${reset.toFixed(1)} ms`);
});

test("the lights and the background are the appearance settings', set when they change", () => {
    const {calls} = h.fake;
    assert.deepEqual(calls.setLighting, [[Math.PI / 2, 1.4]]);
    assert.deepEqual(calls.setBackground.map((c) => c.map((v) => Math.round(v * 255))), [[0x39, 0x39, 0x39]]);
    useAppearanceStore.getState().setBrightness(2);
    assert.deepEqual(calls.setLighting.at(-1), [Math.PI, 2.8]);
    useAppearanceStore.getState().setBackground("#ffffff");
    assert.deepEqual(calls.setBackground.at(-1), [1, 1, 1]);
    assert.equal(calls.setLighting.length, 2, "a background change sets no lights");
});

test("objects without a colour of their own take the default colour, at once and without a reload", () => {
    // Two nodes: grey as ada writes "no colour", and red. Object o0 and o1 are drawn by the first.
    const bytes = glbJson({
        scene: 0,
        scenes: [{extras: {draw_ranges_node0: {o0: [0, 3], o1: [3, 3]}, draw_ranges_node1: {o2: [0, 3], o3: [3, 3]}}}],
        nodes: [{mesh: 0}, {mesh: 1}],
        meshes: [{primitives: [{material: 0}]}, {primitives: [{material: 1}]}],
        materials: [{pbrMetallicRoughness: {baseColorFactor: [0.8, 0.8, 0.8, 1]}}, {pbrMetallicRoughness: {baseColorFactor: [1, 0, 0, 1]}}],
    });
    use(makeScene(4, bytes));
    assert.deepEqual(Object.keys(h.scene.parsedExtras ?? {}), ["draw_ranges_node0", "draw_ranges_node1"], "the extras are handed on parsed");
    assert.equal(picture(), "", "at the default setting the file's grey is drawn as it is: nothing is written");
    const writes = h.fake.writes();
    useAppearanceStore.getState().setDefaultColor("#ff0000");
    assert.equal(picture(), "#0000ff #0000ff own own");
    assert.equal(h.fake.writes() - writes, 2, "one state write per instance");
    api.setObjectColors([ref(0)], "#00ff00");
    assert.equal(picture(), "#00ff00 #0000ff own own", "a given colour wins over the default");
    useAppearanceStore.getState().reset();
    assert.equal(picture(), "#00ff00 own own own");
});

test("a black material is 'no colour' too, drawn in the default colour from the start", () => {
    const bytes = glbJson({
        scenes: [{extras: {draw_ranges_node0: {o0: [0, 3], o1: [3, 3], o2: [6, 3], o3: [9, 3]}}}],
        nodes: [{mesh: 0}],
        meshes: [{primitives: [{material: 0}]}],
        materials: [{pbrMetallicRoughness: {baseColorFactor: [0, 0, 0, 1]}}],
    });
    use(makeScene(4, bytes));
    // 0.8 linear is 204 of 255.
    assert.equal(picture(), "#cccccc #cccccc #cccccc #cccccc");
});
