// Undo / redo on the three.js-free viewport: the real scene and the real viewport interface over it,
// with a stand-in for the renderer that remembers the state each segment was last given. Every
// operation that changes the selection or what is hidden is one step, and undo and redo put the
// picture back (what the renderer was told to draw selected and hidden).
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

const {useBBoxSelectionStore} = await import("@/state/bboxSelectionStore");
const {useObjectInfoStore} = await import("@/state/objectInfoStore");
const {useSectionStore} = await import("@/state/sectionStore");
const {useViewerHistoryStore} = await import("@/state/viewerHistoryStore");
const {addBoxAroundSelection, bindActiveBoxSelection} = await import("@/utils/bboxSelection/applyBoxSelection");
const {redoViewerAction, undoViewerAction} = await import("@/utils/scene/viewerHistory");
const {SegmentFlag} = await import("@/viewport/adacpp/types");
const {setActiveViewport} = await import("@/viewport/api/viewportApi");
const {NativeScene, objectKey} = await import("@/viewport/native/nativeScene");
const {createNativeViewportApi} = await import("@/viewport/native/nativeViewportApi");
const {bindNativeHistory} = await import("@/viewport/native/nativeViewportHistory");
type ViewportApi = import("@/viewport/api/viewportApi").ViewportApi;
type Scene = import("@/viewport/native/nativeScene").NativeScene;

const NAMES = ["a", "b", "c", "d"];

/** A renderer that draws nothing: it hands out a model of four objects in a row along x (under a
 *  root that draws nothing itself), and keeps the state last set for each segment. */
function fakeRenderer() {
    const heap = new ArrayBuffer(1 << 16);
    const module = {_malloc: () => 1024, _free: () => {}, HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap)};
    const states = new Map<number, Uint32Array>();
    let instances = 0;
    let picked: {instance: number; segment: number} | null = null;
    const own = {
        loadGlb: () => {
            const instance = ++instances;
            const bounds = new Float32Array([0, 0, 0, NAMES.length, 1, 1, ...NAMES.flatMap((_, i) => [i, 0, 0, i + 1, 1, 1])]);
            return {
                bounds: [0, 0, 0, NAMES.length, 1, 1],
                extras: "",
                geometries: [{geometry: instance, instance, segmentObjects: Int32Array.from(NAMES, (_, i) => i + 1)}],
                objects: {
                    count: NAMES.length + 1,
                    ids: ["root", ...NAMES],
                    names: ["root", ...NAMES],
                    parents: Int32Array.from([-1, ...NAMES.map(() => 0)]),
                    bounds,
                    triangles: new Uint32Array(NAMES.length + 1),
                    segmentOffsets: Uint32Array.from([0, 0, ...NAMES.map((_, i) => i + 1)]),
                    segments: Uint32Array.from(NAMES.flatMap((_, i) => [instance, i])),
                },
            };
        },
        setSegmentStates: (instance: number, p: number, count: number) => void states.set(instance, module.HEAPU32.slice(p >> 2, (p >> 2) + count * 2)),
        clear: () => states.clear(),
        removeInstance: (instance: number) => void states.delete(instance),
        pick: (_x: number, _y: number, _w: number, _h: number, done: (r: unknown) => void) => done(picked ? {hit: true, ...picked} : {hit: false}),
        cameraUpdate: () => false,
    };
    // Whatever else the scene asks of the renderer (the camera, sizes, colours) does nothing here.
    const renderer = new Proxy(own, {get: (target, name) => (target as Record<string | symbol, unknown>)[name] ?? (() => undefined)});
    return {module, renderer, states, pickNext: (at: typeof picked) => void (picked = at)};
}

function makeScene() {
    const fake = fakeRenderer();
    const listeners = new Map<string, (e: unknown) => void>();
    const input = {
        addEventListener: (type: string, fn: (e: unknown) => void) => void listeners.set(type, fn),
        removeEventListener: () => {},
        setPointerCapture: () => {},
    };
    const container = {clientWidth: 800, clientHeight: 600};
    const canvas = {width: 0, height: 0, remove: () => {}, getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 600})};
    const device = {lost: new Promise(() => {}), destroy: () => {}};
    const Scene = NativeScene as unknown as new (...args: unknown[]) => Scene;
    const scene = new Scene(container, input, canvas, fake.module, fake.renderer, device);
    /** A click at the middle of the view, on `segment` of `instance` (or on nothing). */
    const click = async (at: {instance: number; segment: number} | null, modifiers: {ctrlKey?: boolean} = {}) => {
        fake.pickNext(at);
        const e = {pointerId: 1, button: 0, pointerType: "mouse", clientX: 400, clientY: 300, ...modifiers};
        listeners.get("pointerdown")!(e);
        listeners.get("pointerup")!(e);
        await new Promise((resolve) => setTimeout(resolve, 0));
    };
    return {scene, fake, click};
}

const hist = () => useViewerHistoryStore.getState();
const labels = () => hist().past.map((e) => e.label);
const ref = (id: string, model = "m1") => ({model, id});

let h: ReturnType<typeof makeScene>;
let api: ViewportApi;
let release: () => void = () => {};

/** What the renderer was last told: per object of model `m1` (its one instance), how it is drawn. */
function picture(instance = 1): string {
    // A model no state was ever set for is drawn as loaded: nothing selected, nothing hidden.
    const states = h.fake.states.get(instance) ?? new Uint32Array(NAMES.length * 2);
    return NAMES.map((name, i) => `${name}:${states[i * 2 + 1] & SegmentFlag.Hidden ? "hidden" : states[i * 2] ? "selected" : "shown"}`).join(" ");
}

beforeEach(() => {
    release();
    useBBoxSelectionStore.setState({boxes: [], activeId: null, menu: null});
    useSectionStore.setState({planes: [], activeId: null});
    useObjectInfoStore.getState().setName(null);
    h = makeScene();
    h.scene.load(new Uint8Array(4), "m1", {sourceName: "one.glb"});
    api = createNativeViewportApi(h.scene);
    const withdraw = setActiveViewport(api);
    const unbind = bindNativeHistory(h.scene);
    release = () => {
        unbind();
        withdraw();
        h.scene.dispose();
    };
    hist().reset();
});

test("select, hide, isolate and show all are one step each; undo and redo restore each picture", () => {
    const pictures = [picture()];
    const step = (act: () => void) => {
        act();
        pictures.push(picture());
    };
    step(() => api.setSelection([ref("a"), ref("b")]));
    step(() => api.hide([ref("a")]));
    step(() => api.isolate([ref("c")]));
    step(() => api.showAll());
    assert.deepEqual(pictures, [
        "a:shown b:shown c:shown d:shown",
        "a:selected b:selected c:shown d:shown",
        "a:hidden b:shown c:shown d:shown",
        "a:hidden b:hidden c:shown d:hidden",
        "a:shown b:shown c:shown d:shown",
    ]);
    assert.deepEqual(labels(), ["select 2 objects", "hide 1 object", "isolate (hide 2 objects)", "show all (3 objects)"]);

    for (let i = pictures.length - 2; i >= 0; i--) {
        undoViewerAction();
        assert.equal(picture(), pictures[i], `after undo ${pictures.length - 1 - i}`);
    }
    assert.equal(undoViewerAction(), null);
    for (let i = 1; i < pictures.length; i++) {
        redoViewerAction();
        assert.equal(picture(), pictures[i], `after redo ${i}`);
    }
    assert.equal(redoViewerAction(), null);
});

test("hide selected, hide others, show and clearing the selection are steps too", () => {
    api.setSelection([ref("b")]);
    api.hideOthers();
    assert.equal(picture(), "a:hidden b:selected c:hidden d:hidden");
    api.show([ref("c"), ref("d")]);
    api.hideSelected();
    assert.equal(picture(), "a:hidden b:hidden c:shown d:shown");
    api.setSelection([ref("c")]);
    api.clearSelection();
    assert.deepEqual(labels(), [
        "select 1 object", "hide 3 objects outside the selection", "show 2 objects", "hide 1 object", "select 1 object", "clear selection",
    ]);
    undoViewerAction();
    undoViewerAction();
    undoViewerAction();
    assert.equal(picture(), "a:hidden b:selected c:shown d:shown", "undoing the hide brings the selection back with it");
    undoViewerAction();
    undoViewerAction();
    assert.equal(picture(), "a:shown b:selected c:shown d:shown");
    assert.equal(api.selection().length, 1);
});

test("an operation that changes nothing is no step", () => {
    api.showAll();
    api.hideSelected();
    api.show([ref("a")]);
    api.setSelection([]);
    api.setSelection([ref("root"), ref("no such object")]);
    assert.deepEqual(labels(), []);
});

test("a click is one step: it selects, adds with Ctrl, and clears on empty space", async () => {
    const picks: (string | null)[] = [];
    h.scene.onPick = (key: string | null) => void picks.push(key);
    await h.click({instance: 1, segment: 0});
    await h.click({instance: 1, segment: 2}, {ctrlKey: true});
    assert.equal(picture(), "a:selected b:shown c:selected d:shown");
    await h.click(null);
    assert.equal(picture(), "a:shown b:shown c:shown d:shown");
    await h.click(null);
    assert.deepEqual(labels(), ["select 1 object", "select 2 objects", "clear selection"], "a click that changes nothing is no step");
    assert.deepEqual(picks, [objectKey("m1", "a"), objectKey("m1", "c"), null, null], "each click is still announced");
    undoViewerAction();
    assert.equal(picture(), "a:selected b:shown c:selected d:shown");
    undoViewerAction();
    assert.equal(picture(), "a:selected b:shown c:shown d:shown");
});

test("a selection keeps its state for the object info through undo and redo", () => {
    const info = useObjectInfoStore.getState();
    api.setSelection([ref("a")], {within: () => info.setName("a")});
    api.setSelection([ref("b")], {within: () => info.setName("b")});
    undoViewerAction();
    assert.equal(useObjectInfoStore.getState().name, "a");
    redoViewerAction();
    assert.equal(useObjectInfoStore.getState().name, "b");
});

test("a box around the selection, a drag of its handles and deleting it are one step each", () => {
    const unfollow = bindActiveBoxSelection();
    try {
        api.setSelection([ref("b")]);
        const id = addBoxAroundSelection()!;
        assert.equal(picture(), "a:selected b:selected c:selected d:shown", "the box takes what it touches");
        // A handle dragged: the box's bounds change on every move, and its selection follows.
        const store = useBBoxSelectionStore.getState();
        for (const x of [2.2, 2.6, 3.2, 3.8]) store.update(id, {max: [x, 1.1, 1.1]});
        assert.equal(picture(), "a:selected b:selected c:selected d:selected");
        store.remove(id);
        assert.deepEqual(labels(), ["select 1 object", "add selection box", "edit selection box", "delete selection box"]);

        undoViewerAction();
        assert.equal(useBBoxSelectionStore.getState().boxes.length, 1);
        undoViewerAction();
        assert.equal(useBBoxSelectionStore.getState().boxes[0].max[0] < 2.2, true, "the box is back where the drag began");
        assert.equal(picture(), "a:selected b:selected c:selected d:shown");
        undoViewerAction();
        assert.deepEqual(useBBoxSelectionStore.getState().boxes, []);
        assert.equal(picture(), "a:shown b:selected c:shown d:shown");
        redoViewerAction();
        redoViewerAction();
        assert.equal(useBBoxSelectionStore.getState().boxes[0].max[0], 3.8);
        assert.equal(picture(), "a:selected b:selected c:selected d:selected");
    } finally {
        unfollow();
    }
});

test("a section plane is a step on this viewport as well", () => {
    useSectionStore.getState().addPlane("x");
    assert.deepEqual(labels(), ["add section plane"]);
    undoViewerAction();
    assert.deepEqual(useSectionStore.getState().planes, []);
    redoViewerAction();
    assert.equal(useSectionStore.getState().planes.length, 1);
});

test("a model that joins keeps the history; one that replaces the scene starts it over", () => {
    api.setSelection([ref("a")]);
    api.hide([ref("b")]);
    h.scene.load(new Uint8Array(4), "m2", {add: true, sourceName: "two.glb"});
    assert.equal(hist().past.length, 2);
    api.hide([ref("c", "m2")]);
    assert.equal(picture(2), "a:shown b:shown c:hidden d:shown");

    // The second model leaves: the step that hid one of its objects has nothing to act on, and
    // the steps before it still work.
    api.unloadSource("two.glb");
    undoViewerAction();
    assert.equal(picture(), "a:shown b:hidden c:shown d:shown");
    undoViewerAction();
    assert.equal(picture(), "a:selected b:shown c:shown d:shown");

    h.scene.load(new Uint8Array(4), "m3", {sourceName: "three.glb"});
    assert.deepEqual([hist().past.length, hist().future.length], [0, 0]);
});

test("a selection alone does not look at what is hidden, and another scene's state is ignored", () => {
    api.hide([ref("d")]);
    const before = api.captureState({selection: true});
    api.hide([ref("a")]);
    const change = api.stateChange(before);
    assert.deepEqual([change.selection, change.visibility, change.hidden], [false, false, 0]);

    const other = makeScene();
    other.scene.load(new Uint8Array(4), "m1");
    const foreign = createNativeViewportApi(other.scene);
    other.scene.pickStep = null;
    const state = foreign.captureState({selection: true, visibility: true});
    api.setSelection([ref("b")]);
    api.restoreState(state);
    assert.equal(picture(), "a:hidden b:selected c:shown d:hidden");
    other.scene.dispose();
});
