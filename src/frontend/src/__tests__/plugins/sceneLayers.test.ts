// A plugin's layers whose data changes over time, its tracks on the playback clock and its rows in
// the Layers panel: drawn through the viewport on screen, and all of it gone with the owner.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

import {removeOwnerOverlays} from "@/plugins/sceneOverlays";
import {addImage, addLineLayer, addPointLayer, imageUnavailable, makePluginLayers, makePluginPlayback, polylineSegments} from "@/plugins/sceneLayers";
import {listLayers, resetLayerRegistry} from "@/state/layerRegistry";
import {playbackClock} from "@/state/playbackClock";
import {setSceneOffset} from "@/state/sceneFrameStore";
import {linearChannel} from "@/utils/scene/sceneColor";
import type {ImageLayer, ImageLookChange, WorldImage} from "@/viewport/api/imageLayer";
import type {OverlayLayer, OverlayLines, OverlayLookChange, OverlayPoints} from "@/viewport/api/overlayLayer";
import {setActiveViewport, type ViewportApi, type ViewportKind} from "@/viewport/api/viewportApi";

interface Drawn {
    kind: "lines" | "points" | "image";
    spec: OverlayLines | OverlayPoints | WorldImage;
    geometry: {positions: number[]; count: number; colors: number[] | null}[];
    looks: (OverlayLookChange | ImageLookChange)[];
    pixels: {bytes: number[]; width?: number; height?: number}[];
    removed: boolean;
}

/** A viewport that records what it is asked to draw. */
function fakeViewport(kind: ViewportKind = "native", imageLimit = Infinity) {
    const drawn: Drawn[] = [];
    const disposed: string[] = [];
    const put = (d: Omit<Drawn, "geometry" | "looks" | "pixels" | "removed">): number => drawn.push({...d, geometry: [], looks: [], pixels: [], removed: false});
    const overlays = (name: string): OverlayLayer => ({
        addLines: (spec) => put({kind: "lines", spec: {...spec, segments: new Float32Array(spec.segments)}}),
        addPoints: (spec) => put({kind: "points", spec: {...spec, positions: new Float32Array(spec.positions), colors: spec.colors && new Float32Array(spec.colors)}}),
        addMesh: () => 0,
        setPositions: () => {},
        setGeometry: (handle, positions, count, colors) => {
            const d = drawn[handle - 1];
            const stride = d.kind === "lines" ? 6 : 3;
            const n = count ?? positions.length / stride;
            d.geometry.push({positions: [...positions.subarray(0, n * stride)], count: n, colors: colors ? [...colors.subarray(0, n * 3)] : null});
        },
        setLook: (handle, look) => void drawn[handle - 1].looks.push(look),
        remove: (handle) => void (drawn[handle - 1].removed = true),
        clear: () => {},
        dispose: () => void disposed.push(`overlays:${name}`),
    });
    const images = (name: string): ImageLayer => ({
        add: (image) => put({kind: "image", spec: {...image, pixels: Uint8Array.from(image.pixels)}}),
        setPixels: (handle, pixels, width, height) => void drawn[handle - 1].pixels.push({bytes: [...pixels], width, height}),
        setRect: () => {},
        setLook: (handle, look) => void drawn[handle - 1].looks.push(look),
        unavailable: (w, h) => (w * h > imageLimit ? "too large" : null),
        remove: (handle) => void (drawn[handle - 1].removed = true),
        clear: () => {},
        dispose: () => void disposed.push(`images:${name}`),
    });
    const layers = new Map<string, OverlayLayer | ImageLayer>();
    const once = <T extends OverlayLayer | ImageLayer>(key: string, make: () => T): T => {
        if (!layers.has(key)) layers.set(key, make());
        return layers.get(key) as T;
    };
    const api = {
        kind,
        unavailable: () => null,
        overlays: (name: string) => once(`o:${name}`, () => overlays(name)),
        images: (name: string) => once(`i:${name}`, () => images(name)),
    } as unknown as ViewportApi;
    return {api, drawn, disposed, withdraw: setActiveViewport(api)};
}

let withdraw: (() => void) | null = null;
const viewport = (...args: Parameters<typeof fakeViewport>) => {
    const made = fakeViewport(...args);
    withdraw = made.withdraw;
    return made;
};
const lastLook = (d: Drawn) => Object.assign({}, ...d.looks) as OverlayLookChange;

(globalThis as {localStorage?: unknown}).localStorage = undefined;

afterEach(() => {
    removeOwnerOverlays("p");
    removeOwnerOverlays("q");
    withdraw?.();
    withdraw = null;
    setSceneOffset(null);
    resetLayerRegistry();
});

test("one open line's vertices are its segments", () => {
    const out = new Float32Array(12);
    assert.equal(polylineSegments([0, 0, 0, 1, 0, 0, 1, 1, 0], 3, out), 2);
    assert.deepEqual([...out], [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 0]);
    assert.equal(polylineSegments([0, 0, 0], 1, out), 0);
    assert.equal(polylineSegments([], 0, out), 0);
});

test("a line layer is drawn through the viewport's overlays and replaced in place", () => {
    const {drawn} = viewport();
    const path = addLineLayer("p", {polyline: [0, 0, 0, 1, 0, 0, 1, 1, 0], color: "#ff0000", widthPx: 4, opacity: 0.5, throughModel: true});
    const [lines] = drawn;
    const spec = lines.spec as OverlayLines;
    assert.deepEqual([...spec.segments], [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 0]);
    assert.deepEqual(spec.color, [1, 0, 0], "sRGB in, linear to the viewport");
    assert.equal(spec.widthPx, 4);
    assert.equal(spec.opacity, 0.5);
    assert.equal(spec.onTop, true);

    path.setPolyline(new Float32Array([5, 5, 5, 6, 5, 5, 7, 5, 5, 8, 5, 5, 0, 0, 0]), 4);
    assert.deepEqual(lines.geometry[0], {positions: [5, 5, 5, 6, 5, 5, 6, 5, 5, 7, 5, 5, 7, 5, 5, 8, 5, 5], count: 3, colors: null});
    path.setSegments(new Float32Array([1, 2, 3, 4, 5, 6, 9, 9, 9, 9, 9, 9]), 1);
    assert.deepEqual(lines.geometry[1], {positions: [1, 2, 3, 4, 5, 6], count: 1, colors: null});
    path.setPolyline(new Float32Array(0));
    assert.equal(lines.geometry[2].count, 0);

    path.setLook({visible: false, opacity: 0.25, color: [0, 1, 0]});
    assert.deepEqual(lastLook(lines), {color: [0, 1, 0], visible: false, opacity: 0.25});
    path.remove();
    assert.equal(lines.removed, true);
});

test("a layer may be made with nothing to draw yet", () => {
    const {drawn} = viewport();
    const scan = addPointLayer("p", {sizePx: 3});
    const path = addLineLayer("p");
    assert.equal(drawn[0].geometry[0].count, 0);
    assert.equal(drawn[1].geometry[0].count, 0);
    scan.setPoints(new Float32Array([1, 2, 3]));
    assert.deepEqual(drawn[0].geometry[1].positions, [1, 2, 3]);
    path.setSegments(new Float32Array([0, 0, 0, 1, 1, 1]));
    assert.equal(drawn[1].geometry[1].count, 1);
});

test("points take a colour each when they were made with colours", () => {
    const {drawn} = viewport();
    const scan = addPointLayer("p", {positions: [0, 0, 0, 1, 0, 0], colors: [1, 0, 0, 0.5, 0.5, 0.5]});
    const spec = drawn[0].spec as OverlayPoints;
    assert.deepEqual([...spec.colors!], [1, 0, 0, ...[0.5, 0.5, 0.5].map(linearChannel)].map(Math.fround));
    scan.setPoints(new Float32Array([2, 2, 2, 3, 3, 3, 4, 4, 4]), 3, new Float32Array([0, 0, 1, 0, 1, 0, 1, 0, 0]));
    assert.deepEqual(drawn[0].geometry[0], {positions: [2, 2, 2, 3, 3, 3, 4, 4, 4], count: 3, colors: [0, 0, 1, 0, 1, 0, 1, 0, 0]});
    // A layer made without colours takes none later, and its one colour can change.
    const plain = addPointLayer("p", {positions: [0, 0, 0]});
    plain.setPoints(new Float32Array([1, 1, 1]), 1, new Float32Array([1, 0, 0]));
    assert.equal(drawn[1].geometry[0].colors, null);
    assert.throws(() => addPointLayer("p", {positions: [0, 0, 0], colors: [1, 0]}), /3 numbers per point/);
});

test("an image is drawn through the viewport's images, its pixels replaced in place", () => {
    const {drawn} = viewport();
    const map = addImage("p", {origin: [0, 0, 0], u: [2, 0, 0], v: [0, 1, 0], width: 2, height: 1, pixels: Uint8Array.from([1, 2, 3, 4, 5, 6, 7, 8]), nearest: true});
    const spec = drawn[0].spec as WorldImage;
    assert.deepEqual([spec.width, spec.height, spec.nearest, spec.onTop], [2, 1, true, false]);
    assert.deepEqual([...spec.pixels], [1, 2, 3, 4, 5, 6, 7, 8]);
    map.setPixels(Uint8Array.from([8, 7, 6, 5, 4, 3, 2, 1]));
    assert.deepEqual(drawn[0].pixels[0].bytes, [8, 7, 6, 5, 4, 3, 2, 1]);
    map.setLook({opacity: 0.5});
    assert.deepEqual(lastLook(drawn[0]), {visible: true, opacity: 0.5});
    assert.throws(() => addImage("p", {origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 2, height: 2, pixels: new Uint8Array(4)}), /4 bytes per pixel/);
});

test("a viewport that cannot draw an image of a size says so", () => {
    viewport("native", 100);
    assert.equal(imageUnavailable(10, 10), null);
    assert.equal(imageUnavailable(11, 10), "too large");
    const map = addImage("p", {origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: new Uint8Array(4)});
    assert.equal(map.unavailable(), null);
    map.setPixels(new Uint8Array(20 * 20 * 4), 20, 20);
    assert.equal(map.unavailable(), "too large");
});

test("on the viewport that recentres its models, layers are placed where the model is, and move with it", () => {
    setSceneOffset([10, 20, 30]);
    const {drawn} = viewport("three");
    addLineLayer("p", {segments: [0, 0, 0, 1, 0, 0]});
    addImage("p", {origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: new Uint8Array(4)});
    assert.deepEqual(Array.from(drawn[0].spec.matrix ?? []).slice(12, 15), [10, 20, 30]);
    assert.deepEqual(Array.from(drawn[1].spec.matrix ?? []).slice(12, 15), [10, 20, 30]);
    // Another model is loaded and recentred: what was drawn follows.
    setSceneOffset([1, 2, 3]);
    assert.deepEqual(Array.from(lastLook(drawn[0]).matrix ?? []).slice(12, 15), [1, 2, 3]);
    assert.deepEqual(Array.from(lastLook(drawn[1]).matrix ?? []).slice(12, 15), [1, 2, 3]);
    setSceneOffset(null);
    assert.equal(lastLook(drawn[0]).matrix, null);
});

test("layers follow the scene's placement on the three.js-free viewport as well", () => {
    // It recentres what it loads by the same rule and publishes the same offset.
    setSceneOffset([10, 20, 30]);
    const {drawn} = viewport("native");
    addPointLayer("p", {positions: [0, 0, 0]});
    assert.deepEqual(Array.from(drawn[0].spec.matrix ?? []).slice(12, 15), [10, 20, 30]);
});

test("a layer's row in the Layers panel has the last word on whether it is drawn", () => {
    const {drawn} = viewport();
    const path = addLineLayer("p", {segments: [0, 0, 0, 1, 0, 0], opacity: 0.5, layer: {id: "path", label: "Path", group: "Run"}});
    const dots = addPointLayer("p", {positions: [0, 0, 0], layer: {id: "path", label: "Path", group: "Run"}});
    assert.deepEqual(listLayers(), [{id: "p:path", label: "Path", group: "Run", visible: true, opacity: 1}]);
    const rows = makePluginLayers("p");
    assert.deepEqual(rows.list(), [{id: "path", label: "Path", group: "Run", visible: true, opacity: 1}]);
    rows.setOpacity("path", 0.5);
    assert.deepEqual(lastLook(drawn[0]), {visible: true, opacity: 0.25}, "the plugin's own opacity times the row's");
    assert.deepEqual(lastLook(drawn[1]), {visible: true, opacity: 0.5});
    rows.setVisible("path", false);
    path.setLook({visible: true, opacity: 1});
    assert.deepEqual(lastLook(drawn[0]), {visible: false, opacity: 0.5}, "switched off in the panel stays off");
    // Another plugin's rows are not this one's.
    assert.deepEqual(makePluginLayers("q").list(), []);
    path.remove();
    assert.equal(listLayers().length, 1, "the row stays while something is drawn in it");
    dots.remove();
    assert.equal(listLayers().length, 0);
});

test("everything an owner made goes when its overlays are released: layers, rows and tracks", () => {
    const {drawn, disposed} = viewport();
    addLineLayer("p", {segments: [0, 0, 0, 1, 0, 0], layer: {id: "a", label: "A"}});
    addPointLayer("p", {positions: [0, 0, 0]});
    addImage("p", {origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: new Uint8Array(4), layer: {id: "b", label: "B"}});
    addPointLayer("q", {positions: [0, 0, 0]});
    const playback = makePluginPlayback("p");
    const shown: number[] = [];
    playback.addTrack({id: "run", duration: 12, show: (t) => void shown.push(t)});
    const other = makePluginPlayback("q").addTrack({id: "run", duration: 3, show: () => {}});
    assert.equal(playback.state().tracks, 2);
    assert.equal(playback.state().duration, 12);

    removeOwnerOverlays("p");
    assert.deepEqual(drawn.map((d) => d.removed), [true, true, true, false]);
    assert.deepEqual(disposed.sort(), ["images:plugin:p:layers", "overlays:plugin:p:layers"], "its viewport layers are released with the last thing in them");
    assert.equal(listLayers().length, 0);
    assert.equal(playback.state().tracks, 1);
    assert.equal(playback.state().duration, 3);
    other.remove();
    assert.equal(playbackClock.state().tracks, 0);
});

test("a plugin's track is on the viewer's one clock", () => {
    viewport();
    const playback = makePluginPlayback("p");
    const shown: number[] = [];
    const track = playback.addTrack({id: "run", duration: 10, show: (t) => void shown.push(t), label: "Run"});
    assert.deepEqual(playbackClock.trackList(), [{id: "p:run", label: "Run", duration: 10}]);
    playback.seek(4);
    assert.equal(playback.time(), 4);
    assert.equal(shown[shown.length - 1], 4);
    playback.setSpeed(2);
    playback.setLoop(true);
    assert.deepEqual([playback.state().speed, playback.state().loop], [2, true]);
    playback.followEnd(true);
    track.setDuration(15);
    assert.equal(playback.time(), 15);
    assert.equal(shown[shown.length - 1], 15);
    playback.pause();
    assert.equal(playback.state().followEnd, false);
    track.remove();
    playbackClock.setSpeed(1);
    playbackClock.setLoop(false);
});

test("without a viewport nothing is drawn and nothing fails", () => {
    const path = addLineLayer("p", {segments: [0, 0, 0, 1, 0, 0]});
    path.setSegments(new Float32Array(6));
    path.setLook({visible: false});
    path.remove();
    const map = addImage("p", {origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: new Uint8Array(4)});
    assert.equal(map.unavailable(), null);
    assert.equal(imageUnavailable(9999, 9999), null);
});

test("a row starts with the look its first layer names, unless the user left a choice for it", () => {
    const {drawn} = viewport();
    const kept = new Map<string, string>([["ada-layer-look", JSON.stringify({"p:scan": {visible: true, opacity: 0.8}})]]);
    (globalThis as {localStorage?: unknown}).localStorage = {getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => void kept.set(k, v)};
    resetLayerRegistry();
    try {
        // Nothing stored for this row: it starts as the layer says, here switched off and half opaque.
        addLineLayer("p", {segments: [0, 0, 0, 1, 0, 0], opacity: 0.5, layer: {id: "path", label: "Path", visible: false, opacity: 0.5}});
        assert.deepEqual(makePluginLayers("p").list()[0], {id: "path", label: "Path", group: undefined, visible: false, opacity: 0.5});
        assert.deepEqual(lastLook(drawn[0]), {visible: false, opacity: 0.25}, "the row's look times the layer's own");
        // The row's start is not the layer's own look: switched on, the layer's own opacity remains.
        makePluginLayers("p").setVisible("path", true);
        makePluginLayers("p").setOpacity("path", 1);
        assert.deepEqual(lastLook(drawn[0]), {visible: true, opacity: 0.5});
        // A second layer in the row does not start it again.
        addPointLayer("p", {positions: [0, 0, 0], layer: {id: "path", label: "Path", visible: false}});
        assert.equal(makePluginLayers("p").list()[0].visible, true);
        // A stored choice wins over what the layer names.
        addPointLayer("p", {positions: [0, 0, 0], layer: {id: "scan", label: "Scan", visible: false, opacity: 0.1}});
        assert.deepEqual(makePluginLayers("p").list()[1], {id: "scan", label: "Scan", group: undefined, visible: true, opacity: 0.8});
    } finally {
        removeOwnerOverlays("p");
        (globalThis as {localStorage?: unknown}).localStorage = undefined;
    }
});
