// The selected objects' boxes in the model's own coordinates on the three.js-free viewport
// (`ViewportApi.selectionBoxes`, `ctx.scene.selectionBoxes`): the scene recentres a model far from
// the origin, and a box comes back where the file has it, named as the file names the object.
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

const {NativeScene, objectKey} = await import("@/viewport/native/nativeScene");
const {createNativeViewportApi} = await import("@/viewport/native/nativeViewportApi");
const {setActiveViewport} = await import("@/viewport/api/viewportApi");
const {selectionBoxes} = await import("@/plugins/sceneOverlays");
type Scene = import("@/viewport/native/nativeScene").NativeScene;

/** In the file: a structure "Mini" with a deck holding two plates, the second without a name. */
const FILE = {
    ids: ["mini", "deck", "p1", "p2"],
    names: ["Mini", "deck_a", "pl_a", ""],
    parents: [-1, 0, 1, 1],
    boxes: [null, null, [400000, 6500000, 10, 400002, 6500001, 11], [400010, 6500005, 10, 400011, 6500006, 10]] as (number[] | null)[],
};
const FILE_BOX = [400000, 6500000, 10, 400011, 6500006, 11];

function makeScene(): Scene {
    const heap = new ArrayBuffer(1 << 20);
    const module = {
        _malloc: () => 1024, _free: () => {},
        HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap),
        glbBounds: () => FILE_BOX,
    };
    const own = {
        // The renderer places the model by the offset it is handed: its boxes are of the world.
        loadGlb: (_p: number, _n: number, opts: {offset?: number[]}) => {
            const o = opts.offset ?? [0, 0, 0];
            const world = (b: number[]) => b.map((v, k) => v + o[k % 3]);
            const bounds = new Float32Array(24);
            FILE.boxes.forEach((b, i) => bounds.set(b ? world(b) : [1, 1, 1, -1, -1, -1], i * 6));
            // The plates under the deck make up the boxes of the levels above them.
            for (const i of [0, 1]) bounds.set(world(FILE_BOX), i * 6);
            return {
                bounds: world(FILE_BOX), extras: "", adaExtData: "", featureEdges: 0, nodeObjects: false,
                geometries: [{geometry: 1, instance: 1, topology: 0, vertexCount: 6, vertexColors: false, featureEdges: false, segmentObjects: Int32Array.from([2, 3])}],
                objects: {
                    count: 4, ids: FILE.ids, names: FILE.names, parents: Int32Array.from(FILE.parents), bounds,
                    triangles: new Uint32Array(4), segmentOffsets: Uint32Array.from([0, 0, 0, 1, 2]), segments: Uint32Array.from([1, 0, 1, 1]),
                },
            };
        },
        cameraPose: () => ({eye: [-5, 5, 5], target: [0, 0, 0], up: [0, 0, 1], fov: 60, orthographic: false, orthoHeight: 0, azimuth: 0, elevation: 0, roll: 0, distance: 1}),
        cameraUpdate: () => false,
    };
    const renderer = new Proxy(own, {get: (target, name) => (target as Record<string | symbol, unknown>)[name] ?? (() => undefined)});
    const input = {addEventListener: () => {}, removeEventListener: () => {}, setPointerCapture: () => {}};
    const container = {clientWidth: 800, clientHeight: 600};
    const canvas = {width: 800, height: 600, remove: () => {}, getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 600})};
    const device = {lost: new Promise(() => {}), destroy: () => {}};
    const Ctor = NativeScene as unknown as new (...args: unknown[]) => Scene;
    const scene = new Ctor(container, input, canvas, module, renderer, device);
    scene.load(new Uint8Array(4), "m1", {sourceName: "model.glb"});
    return scene;
}

const near = (got: number[], want: number[]) => got.forEach((v, k) => assert.ok(Math.abs(v - want[k]) < 1e-2, `${got} != ${want}`));

test("a selection gives a box per object, in the file's coordinates, named as the file names it", () => {
    const scene = makeScene();
    assert.ok(scene.sceneOffset.some((v) => v !== 0), "the model is recentred");
    const api = createNativeViewportApi(scene);
    const withdraw = setActiveViewport(api);
    try {
        assert.deepEqual(api.selectionBoxes(), []);
        api.setSelection([{model: "m1", id: "p1"}, {model: "m1", id: "p2"}]);
        const boxes = selectionBoxes().sort((a, b) => a.id.localeCompare(b.id));
        assert.deepEqual(
            boxes.map(({model, id, name, path}) => ({model, id, name, path})),
            [
                {model: "m1", id: "p1", name: "pl_a", path: ["Mini", "deck_a"]},
                {model: "m1", id: "p2", name: null, path: ["Mini", "deck_a"]},
            ],
        );
        near([...boxes[0].min, ...boxes[0].max], FILE.boxes[2]!);
        near([...boxes[1].min, ...boxes[1].max], FILE.boxes[3]!);
    } finally {
        withdraw();
    }
    assert.deepEqual(selectionBoxes(), [], "no viewport, nothing selected");
});

test("a level of the hierarchy that draws nothing is not an entry", () => {
    const scene = makeScene();
    scene.select(objectKey("m1", "deck"));
    scene.select(objectKey("m1", "p1"), true);
    assert.deepEqual(createNativeViewportApi(scene).selectionBoxes().map((b) => b.id), ["p1"]);
});
