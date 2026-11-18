// The orthographic view on the three.js-free viewport: the renderer's camera follows the projection
// setting, takes its own clip range there (which spans the scene on both sides of the camera), a fit
// sizes the view from the box's outline on the view plane (issue #552), and screen-sized billboards
// keep their size on screen and are hit where they are drawn.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

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
const {createNativeBillboardLayer} = await import("@/viewport/native/nativeBillboards");
const {useProjectionStore} = await import("@/state/projectionStore");
const {DEFAULT_VIEW, ORTHO_FIT_BOX_MARGIN} = await import("@/viewport/api/cameraFrame");
const {billboardsAt, screenScale} = await import("@/viewport/api/billboards");
type Scene = import("@/viewport/native/nativeScene").NativeScene;

const near = (a: number, b: number, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

afterEach(() => useProjectionStore.getState().setOrthographic(false));

/** A scene over a renderer whose camera keeps the pose it is given, as adacpp's does: the orthographic
 *  view's height is 2 * distance * tan(fov / 2) when it is not given. */
function makeScene() {
    const heap = new ArrayBuffer(1 << 16);
    const clips: number[][] = [];
    const cam = {eye: [-5, 5, 5], target: [0, 0, 0], up: [0, 0, 1], fov: 60, orthographic: false, orthoHeight: 0};
    const derive = () => {
        const d = Math.hypot(cam.eye[0] - cam.target[0], cam.eye[1] - cam.target[1], cam.eye[2] - cam.target[2]);
        cam.orthoHeight = 2 * d * Math.tan((cam.fov * Math.PI) / 360);
    };
    const module = {_malloc: () => 1024, _free: () => {}, HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap)};
    const own = {
        cameraPose: () => ({...cam, eye: [...cam.eye], target: [...cam.target], up: [...cam.up], azimuth: 0, elevation: 0, roll: 0, distance: 1}),
        cameraSetPose: (ex: number, ey: number, ez: number, tx: number, ty: number, tz: number, ux: number, uy: number, uz: number, fov: number, ortho: boolean, height: number) => {
            Object.assign(cam, {eye: [ex, ey, ez], target: [tx, ty, tz], up: [ux, uy, uz], fov, orthographic: ortho});
            if (height > 0) cam.orthoHeight = height;
            else derive();
        },
        cameraSetOrthographic: (on: boolean) => {
            cam.orthographic = on;
            derive();
        },
        cameraSetClipRange: (n: number, f: number) => void clips.push([n, f]),
        cameraUpdate: () => false,
        addGeometry: () => 1,
        addInstance: () => 1,
    };
    const renderer = new Proxy(own, {get: (target, name) => (target as Record<string | symbol, unknown>)[name] ?? (() => undefined)});
    const input = {addEventListener: () => {}, removeEventListener: () => {}, setPointerCapture: () => {}};
    const container = {clientWidth: 900, clientHeight: 600};
    const canvas = {width: 900, height: 600, remove: () => {}, getBoundingClientRect: () => ({left: 0, top: 0, width: 900, height: 600})};
    const device = {lost: new Promise(() => {}), destroy: () => {}};
    const Ctor = NativeScene as unknown as new (...args: unknown[]) => Scene;
    const scene = new Ctor(container, input, canvas, module, renderer, device);
    return {scene, cam, clips};
}

/** How far the box reaches towards the view's edges, as a share of the half-width and half-height. */
function reach(cam: {eye: number[]; target: number[]; up: number[]; orthoHeight: number}, box: number[], aspect: number) {
    const sub = (a: number[], b: number[]) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
    const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const cross = (a: number[], b: number[]) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const unit = (a: number[]) => a.map((v) => v / Math.hypot(a[0], a[1], a[2]));
    const forward = unit(sub(cam.target, cam.eye));
    const right = unit(cross(forward, cam.up));
    const up = cross(right, forward);
    const half = cam.orthoHeight / 2;
    let x = 0, y = 0;
    for (let i = 0; i < 8; i++) {
        const c = sub([i & 1 ? box[3] : box[0], i & 2 ? box[4] : box[1], i & 4 ? box[5] : box[2]], cam.target);
        x = Math.max(x, Math.abs(dot(c, right)) / (half * aspect));
        y = Math.max(y, Math.abs(dot(c, up)) / half);
    }
    return {x, y};
}

test("the renderer's camera follows the setting, with its own clip range while orthographic", () => {
    const {cam, clips} = makeScene();
    assert.equal(cam.orthographic, false);
    useProjectionStore.getState().setOrthographic(true);
    assert.equal(cam.orthographic, true);
    assert.deepEqual(clips.at(-1), [0, 0], "the renderer's own range: the scene on both sides of the camera");
    useProjectionStore.getState().setOrthographic(false);
    assert.equal(cam.orthographic, false);
    assert.deepEqual(clips.at(-1), [DEFAULT_VIEW.near, DEFAULT_VIEW.far], "the perspective range again");
});

test("a fit in the orthographic view frames a long, thin box and a cube to the margin", () => {
    const {scene, cam, clips} = makeScene();
    useProjectionStore.getState().setOrthographic(true);
    const aspect = 900 / 600;
    // Front-on: look along +y first, as the front view does.
    scene.setCameraPose([0, -30, 0], [0, 0, 0], 0);
    const beam = [-10, -0.1, -0.1, 10, 0.1, 0.1];
    assert.ok(scene.fitBox(beam as never, 0));
    // A box fit's margin round the outline; the length across the width decides.
    near(reach(cam, beam, aspect).x, 1 / ORTHO_FIT_BOX_MARGIN, 1e-9);
    assert.deepEqual(clips.at(-1), [0, 0], "a fit keeps the orthographic clip range");
    const cube = [-1, -1, -1, 1, 1, 1];
    scene.fitBox(cube as never, 0);
    near(reach(cam, cube, aspect).y, 1 / ORTHO_FIT_BOX_MARGIN, 1e-9);
    // A named view frames the whole model's outline too: no model here, so it turns about the target.
    scene.viewFrom([1, 0, 0], 0);
    near(cam.eye[1], 0, 1e-9);
});

test("a screen-sized billboard keeps its size on screen in the orthographic view, through a zoom", () => {
    const {cam} = makeScene();
    const sprites: {scaleY: number}[] = [];
    const fake = {
        sceneEpoch: 0,
        drawsSprites: true,
        elements: {view: {clientHeight: 600}, input: {addEventListener: () => {}, removeEventListener: () => {}}},
        get cameraPose() {
            return {...cam};
        },
        onBeforeDraw: new Set<() => void>(),
        addTexture: () => 1,
        addSprite: () => 1,
        setSprite: (_s: number, _t: number, _p: unknown, _sx: number, scaleY: number) => void sprites.push({scaleY}),
        removeSprite: () => {},
        removeTexture: () => {},
    };
    const image = {
        key: "label",
        draw: () => ({width: 2, height: 1, getContext: () => ({getImageData: () => ({data: new Uint8ClampedArray(8), width: 2, height: 1})})}),
    };
    const layer = createNativeBillboardLayer(fake as unknown as Scene);
    layer.add({position: [0, 0, 0], image, height: 30, sizing: "screen"} as never);
    near(sprites.at(-1)!.scaleY, screenScale(30, 600, 60), 1e-12);
    const frame = () => fake.onBeforeDraw.forEach((f) => f());
    // Orthographic: 30 px of a view 600 px tall is a twentieth of the world's height it shows.
    cam.orthographic = true;
    cam.orthoHeight = 12;
    frame();
    near(sprites.at(-1)!.scaleY, 0.6, 1e-12);
    const count = sprites.length;
    frame();
    assert.equal(sprites.length, count, "an unchanged camera writes nothing");
    cam.orthoHeight = 6; // zoomed in
    frame();
    near(sprites.at(-1)!.scaleY, 0.3, 1e-12);
    cam.orthographic = false;
    frame();
    near(sprites.at(-1)!.scaleY, screenScale(30, 600, 60), 1e-12);
    layer.dispose();
    assert.equal(fake.onBeforeDraw.size, 0);
});

test("a billboard is hit where an orthographic view draws it, at any depth", () => {
    const camera = {eye: [0, -10, 0] as [number, number, number], target: [0, 0, 0] as [number, number, number], up: [0, 0, 1] as [number, number, number], fovDeg: 60, orthoHeight: 20};
    // 900 x 600: 30 px per world unit. A world-sized billboard 1 high is 30 px high, wherever it is.
    const at = (x: number, y: number, depthY: number) =>
        billboardsAt(camera, 900, 600, [{position: [3, depthY, 2], height: 1, sizing: "world", aspect: 1, tag: "b"}], x, y);
    // (3, 2) is 90 px right of and 60 px above the centre.
    assert.deepEqual(at(450 + 90, 300 - 60, 0), ["b"]);
    assert.deepEqual(at(450 + 90 + 14, 300 - 60, 0), ["b"]);
    assert.deepEqual(at(450 + 90 + 16, 300 - 60, 0), []);
    // Far behind the target and behind the camera alike: no perspective, nothing culled.
    assert.deepEqual(at(450 + 90, 300 - 60, 500), ["b"]);
    assert.deepEqual(at(450 + 90, 300 - 60, -50), ["b"]);
    // The same point in perspective is somewhere else.
    assert.deepEqual(billboardsAt({...camera, orthoHeight: undefined}, 900, 600, [{position: [3, 0, 2], height: 1, sizing: "world", aspect: 1, tag: "b"}], 540, 240), []);
});
