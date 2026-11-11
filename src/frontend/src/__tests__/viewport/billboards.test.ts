// Billboards: the plain-data side (where one lands on screen, which one a click hits), following the
// viewport on screen, and the three.js-free viewport's layer against a stand-in for its scene.
import {test} from "node:test";
import assert from "node:assert/strict";

import {SpriteFlag} from "@/viewport/adacpp/types";
import {billboardsAt, projectToView, screenScale, type BillboardCamera, type BillboardImage} from "@/viewport/api/billboards";
import {followActiveViewport, notifyModelLoaded, setActiveViewport, type ViewportApi} from "@/viewport/api/viewportApi";
import {createNativeBillboardLayer} from "@/viewport/native/nativeBillboards";
import type {NativeScene} from "@/viewport/native/nativeScene";

/** Ten units back along -y, looking at the origin, z up: x is right and z is up on screen. */
const CAMERA: BillboardCamera = {eye: [0, -10, 0], target: [0, 0, 0], up: [0, 0, 1], fovDeg: 90};
const near = (a: number, b: number) => assert.ok(Math.abs(a - b) < 1e-9, `${a} is not ${b}`);

test("a point is projected as a perspective camera sees it", () => {
    const centre = projectToView(CAMERA, 800, 600, [0, 0, 0])!;
    near(centre.x, 400);
    near(centre.y, 300);
    near(centre.depth, 10);
    // 90 degrees: at depth 10 the view is 20 high, so 5 up is a quarter of the height above the middle.
    const up = projectToView(CAMERA, 800, 600, [0, 0, 5])!;
    near(up.x, 400);
    near(up.y, 150);
    const right = projectToView(CAMERA, 800, 600, [5, 0, 0])!;
    near(right.x, 550);
    assert.equal(projectToView(CAMERA, 800, 600, [0, -20, 0]), null, "behind the camera");
});

test("a constant-size billboard's scale gives its height in pixels back", () => {
    // Such a sprite spans scale * focal * viewHeight / 2 pixels.
    for (const fov of [30, 45, 90]) {
        const scale = screenScale(18, 600, fov);
        near((scale * 600) / (2 * Math.tan((fov * Math.PI) / 360)), 18);
    }
});

test("a click hits the billboards under it, the one drawn on top first", () => {
    const at = (x: number, y: number, billboards: Parameters<typeof billboardsAt>[3]) => billboardsAt(CAMERA, 800, 600, billboards, x, y);
    // World-sized: 2 high at depth 10 under 90 degrees is 60 pixels; twice as wide as high.
    const world = {position: [0, 0, 0], height: 2, sizing: "world", aspect: 2, tag: "world"} as const;
    assert.deepEqual(at(400, 300, [world]), ["world"]);
    assert.deepEqual(at(400 + 59, 300 + 29, [world]), ["world"]);
    assert.deepEqual(at(400 + 61, 300, [world]), []);
    assert.deepEqual(at(400, 300 + 31, [world]), []);
    // Screen-sized: its height is pixels wherever it is.
    const screen = {position: [0, 30, 0], height: 20, sizing: "screen", aspect: 1, tag: "screen"} as const;
    assert.deepEqual(at(400, 309, [screen]), ["screen"]);
    assert.deepEqual(at(400, 311, [screen]), []);
    // Anchored at its lower left corner, it lies up and to the right of its point.
    const corner = {...screen, anchor: [0, 0], tag: "corner"} as const;
    assert.deepEqual(at(410, 290, [corner]), ["corner"]);
    assert.deepEqual(at(390, 310, [corner]), []);
    // The nearer one is in front; a higher order beats being nearer.
    const far = {...world, position: [0, 5, 0], tag: "far"} as const;
    assert.deepEqual(at(400, 300, [far, world]), ["world", "far"]);
    assert.deepEqual(at(400, 300, [{...far, order: 2}, {...world, order: 1}]), ["far", "world"]);
});

test("something attached to the viewport on screen moves with it", () => {
    const log: string[] = [];
    const viewport = (kind: string) => ({kind}) as unknown as ViewportApi;
    const attach = (v: ViewportApi) => {
        log.push(`attach ${v.kind}`);
        return () => void log.push(`detach ${v.kind}`);
    };
    const stop = followActiveViewport(attach);
    assert.deepEqual(log, [], "no viewport yet");
    const withdrawThree = setActiveViewport(viewport("three"));
    const withdrawNative = setActiveViewport(viewport("native"));
    withdrawThree(); // already replaced: nothing to do
    withdrawNative();
    assert.deepEqual(log, ["attach three", "detach three", "attach native", "detach native"]);
    const withdraw = setActiveViewport(viewport("three"));
    stop();
    withdraw();
    assert.deepEqual(log.slice(4), ["attach three", "detach three"]);
});

/** A stand-in for the three.js-free viewport's scene: it records what a layer asks the renderer for. */
function fakeScene() {
    const calls: string[] = [];
    let ids = 0;
    const scene = {
        sceneEpoch: 0,
        drawsSprites: true,
        elements: {view: {clientHeight: 600}, input: {}},
        cameraPose: {fov: 90},
        addTexture: (_rgba: Uint8ClampedArray, w: number, h: number) => (calls.push(`texture ${w}x${h}`), ++ids),
        removeTexture: (id: number) => void calls.push(`-texture ${id}`),
        addSprite: () => (calls.push("sprite"), ++ids),
        setSprite: (id: number, texture: number, p: number[], sx: number, sy: number, cx: number, cy: number, flags: number, order: number) =>
            void calls.push(`set ${id} tex ${texture} at ${p.join(",")} scale ${sx.toFixed(3)},${sy.toFixed(3)} centre ${cx},${cy} flags ${flags} order ${order}`),
        removeSprite: (id: number) => void calls.push(`-sprite ${id}`),
    };
    return {scene: scene as unknown as NativeScene, raw: scene, calls};
}

/** An image whose "canvas" is a stand-in with pixels; counts how often it is painted. */
function fakeImage(key: string, width: number, height: number, untagged = false): BillboardImage & {painted: number; pixel: number[]} {
    const image = {
        key,
        untagged,
        painted: 0,
        pixel: [] as number[],
        draw: () => {
            image.painted++;
            const data = new Uint8ClampedArray(width * height * 4).fill(128);
            const canvas = {width, height, getContext: () => ({getImageData: () => ((image.pixel = [...data.slice(0, 4)]), {data, width, height})})};
            return canvas as unknown as HTMLCanvasElement;
        },
    };
    return image;
}

class NoResizeObserver {
    observe(): void {}
    disconnect(): void {}
}
(globalThis as {ResizeObserver?: unknown}).ResizeObserver ??= NoResizeObserver;

test("the three.js-free viewport draws billboards as sprites, one texture per image", () => {
    const {scene, calls} = fakeScene();
    const layer = createNativeBillboardLayer(scene);
    const image = fakeImage("icon", 128, 64);
    const a = layer.add({position: [1, 2, 3], image, height: 2, sizing: "world"});
    const b = layer.add({position: [4, 5, 6], image, height: 18, sizing: "screen", anchor: [0, 1], order: 7});
    assert.equal(image.painted, 1, "the image is painted once for both");
    assert.deepEqual(calls, [
        "texture 128x64",
        "sprite",
        `set 2 tex 1 at 1,2,3 scale 4.000,2.000 centre 0.5,0.5 flags ${SpriteFlag.SizeAttenuation} order 9995`,
        "sprite",
        // 18 pixels of a 600 pixel view under 90 degrees: 2 * 18 / 600, and twice that wide.
        "set 3 tex 1 at 4,5,6 scale 0.120,0.060 centre 0,1 flags 0 order 7",
    ]);

    calls.length = 0;
    layer.update(a, {position: [9, 9, 9], image, height: 2, sizing: "world"});
    assert.deepEqual(calls, [`set 2 tex 1 at 9,9,9 scale 4.000,2.000 centre 0.5,0.5 flags ${SpriteFlag.SizeAttenuation} order 9995`]);
    assert.equal(image.painted, 1);

    calls.length = 0;
    layer.remove(a);
    assert.deepEqual(calls, ["-sprite 2"], "the image is still in use");
    layer.remove(b);
    assert.deepEqual(calls, ["-sprite 2", "-sprite 3", "-texture 1"]);
    layer.dispose();
});

test("after a load emptied the renderer, the billboards are made again and nothing stale is removed", () => {
    const {scene, raw, calls} = fakeScene();
    const layer = createNativeBillboardLayer(scene);
    const image = fakeImage("icon", 64, 64);
    layer.add({position: [0, 0, 0], image, height: 1, sizing: "world"});
    calls.length = 0;

    raw.sceneEpoch++;
    notifyModelLoaded({model: "m", sourceName: "m.glb", sceneExtras: ""});
    assert.deepEqual(calls.map((c) => c.split(" ")[0]), ["texture", "sprite", "set"], "a new texture and sprite, no removals");
    assert.equal(image.painted, 1, "from the pixels kept, not a repaint");

    // A change that arrives before the announcement finds the same: the old numbers are not used.
    calls.length = 0;
    raw.sceneEpoch++;
    layer.clear();
    assert.deepEqual(calls.filter((c) => c.startsWith("-")), ["-sprite 6", "-texture 5"], "only what was made after the load");
    layer.dispose();
});

test("an image three.js would read as linear is encoded for the renderer", () => {
    const {scene} = fakeScene();
    const layer = createNativeBillboardLayer(scene);
    let uploaded: number[] = [];
    (scene as unknown as {addTexture: (rgba: Uint8ClampedArray) => number}).addTexture = (rgba) => ((uploaded = [...rgba.slice(0, 4)]), 1);
    layer.add({position: [0, 0, 0], image: fakeImage("untagged", 1, 1, true), height: 1, sizing: "world"});
    assert.deepEqual(uploaded, [188, 188, 188, 128], "colour encoded, alpha left alone");
    layer.add({position: [0, 0, 0], image: fakeImage("srgb", 1, 1), height: 1, sizing: "world"});
    assert.deepEqual(uploaded, [128, 128, 128, 128]);
    layer.dispose();
});
