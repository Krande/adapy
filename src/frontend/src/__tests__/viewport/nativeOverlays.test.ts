// Overlay lines and points on the three.js-free viewport: each is a geometry of the renderer with
// the look of an annotation, moved through its morph, and made again after a load emptied the scene.
import assert from "node:assert/strict";
import {test} from "node:test";

import {InstanceFlag, type AdacppModule, type WebRendererApi} from "@/viewport/adacpp/types";
import {colorsPerEndpoint, segmentDistances} from "@/viewport/api/overlayLayer";
import type {ResultMeshData, ResultMeshLayer} from "@/viewport/api/resultLayer";
import {createNativeOverlayLayer} from "@/viewport/native/nativeOverlays";
import {NativeResultLayer} from "@/viewport/native/nativeResultLayer";
import type {NativeScene} from "@/viewport/native/nativeScene";

interface Drawn {
    data: ResultMeshData;
    colors: number[];
    displacement: number[];
    influence: number;
    removed: boolean;
    visible: boolean;
    opacity: number | undefined;
    color: readonly number[] | null;
    matrix: ArrayLike<number> | null | undefined;
}

/** A scene that records the layers it is asked for. */
function fakeScene() {
    const drawn: Drawn[] = [];
    const scene = {
        sceneEpoch: 0,
        onLoaded: new Set<() => void>(),
        createResultLayer: (data: ResultMeshData): ResultMeshLayer => {
            const d: Drawn = {data, colors: [], displacement: [], influence: 0, removed: false, visible: true, opacity: data.opacity, color: null, matrix: undefined};
            drawn.push(d);
            return {
                vertexCount: data.positions.length / 3,
                setColors: (rgb) => void (d.colors = [...rgb]),
                setDisplacement: (xyz) => void (d.displacement = [...xyz]),
                setInfluence: (v) => void (d.influence = v),
                setColorsVisible: () => {},
                setColor: (rgb) => void (d.color = rgb),
                setOpacity: (opacity) => void (d.opacity = opacity),
                setVisible: (visible) => void (d.visible = visible),
                setMatrix: (matrix: ArrayLike<number> | null) => void (d.matrix = matrix),
                remove: () => void (d.removed = true),
            } as ResultMeshLayer;
        },
    };
    return {scene, drawn, layer: createNativeOverlayLayer(scene as unknown as NativeScene)};
}

test("a segment's colour is at both its ends, and dashes are measured along each segment", () => {
    assert.deepEqual([...colorsPerEndpoint([1, 0, 0, 0, 1, 0], new Float32Array(12))], [1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0]);
    assert.deepEqual([...segmentDistances(new Float32Array([0, 0, 0, 3, 4, 0, 1, 1, 1, 1, 1, 3]), new Float32Array(4))], [0, 5, 0, 2]);
});

test("lines are a line geometry in the look of an annotation", () => {
    const {drawn, layer} = fakeScene();
    const segments = new Float32Array([0, 0, 0, 1, 0, 0]);
    const handle = layer.addLines({segments, colors: new Float32Array([0, 1, 0]), widthPx: 3, onTop: true, clipped: true, dashed: {dash: 0.2, gap: 0.1}});
    segments.fill(9); // read during the call, not kept
    const [lines] = drawn;
    assert.equal(lines.data.topology, "lines");
    assert.deepEqual([...lines.data.positions], [0, 0, 0, 1, 0, 0]);
    assert.equal(lines.data.sizePx, 3);
    assert.deepEqual(lines.data.overlay?.onTop, true);
    assert.deepEqual([...lines.data.overlay!.dashed!.distances], [0, 1]);
    assert.deepEqual(lines.colors, [0, 1, 0, 0, 1, 0]);
    assert.deepEqual(lines.data.color, [1, 1, 1], "white under its own colours");

    layer.setPositions(handle, new Float32Array([0, 0, 2, 1, 0, 3]));
    assert.deepEqual(lines.displacement, [0, 0, 2, 0, 0, 3], "moved through the morph: no geometry is made again");
    assert.equal(lines.influence, 1);
    assert.equal(drawn.length, 1);

    layer.remove(handle);
    assert.equal(lines.removed, true);
});

test("a shape is a triangle geometry in the look of an annotation, with a colour per vertex", () => {
    const {drawn, layer} = fakeScene();
    const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
    const indices = new Uint32Array([0, 1, 2]);
    layer.addMesh({positions, indices, colors: new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1]), opacity: 0.5, onTop: true});
    positions.fill(9); // read during the call, not kept
    indices.fill(0);
    const [mesh] = drawn;
    assert.equal(mesh.data.topology, "triangles");
    assert.deepEqual([...mesh.data.positions], [0, 0, 0, 1, 0, 0, 0, 1, 0]);
    assert.deepEqual([...mesh.data.indices!], [0, 1, 2]);
    assert.equal(mesh.data.opacity, 0.5);
    assert.equal(mesh.data.overlay?.onTop, true);
    assert.deepEqual(mesh.colors, [1, 0, 0, 0, 1, 0, 0, 0, 1]);
    assert.deepEqual(mesh.data.color, [1, 1, 1], "white under its own colours");
});

test("what is drawn is made again after a load emptied the scene, where it was moved to", () => {
    const {scene, drawn, layer} = fakeScene();
    const handle = layer.addPoints({positions: new Float32Array([1, 2, 3]), color: [1, 0, 0], sizePx: 8});
    layer.setPositions(handle, new Float32Array([1, 2, 5]));
    // A model unloaded beside others empties nothing.
    for (const listener of scene.onLoaded) listener();
    assert.equal(drawn.length, 1);
    scene.sceneEpoch++;
    for (const listener of scene.onLoaded) listener();
    assert.equal(drawn.length, 2);
    assert.equal(drawn[1].data.topology, "points");
    assert.deepEqual(drawn[1].data.color, [1, 0, 0]);
    assert.deepEqual(drawn[1].displacement, [0, 0, 2]);
    layer.dispose();
    assert.equal(drawn[1].removed, true);
    assert.equal(scene.onLoaded.size, 0);
});

test("an annotation's instance is unlit, not picked, and in front of the faces it lies on", () => {
    const heap = new ArrayBuffer(1 << 12);
    let top = 8;
    const module = {
        HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap),
        _malloc: (bytes: number) => ((top += Math.ceil(bytes / 8) * 8), top - Math.ceil(bytes / 8) * 8),
        _free: () => {},
    } as unknown as AdacppModule;
    const seen = {flags: 0, dash: 0, gap: 0, distances: [] as number[]};
    const renderer = {
        addGeometry: (...args: number[]) => {
            if (args[10]) seen.distances = [...module.HEAPF32.subarray(args[10] >> 2, (args[10] >> 2) + args[2])];
            return 1;
        },
        addInstance: () => 2,
        setInstance: (...args: number[]) => void Object.assign(seen, {flags: args[6], dash: args[12], gap: args[13]}),
    } as unknown as WebRendererApi;
    const positions = new Float32Array([0, 0, 0, 2, 0, 0]);
    new NativeResultLayer(module, renderer, {
        positions, indices: null, topology: "lines",
        overlay: {dashed: {dash: 0.5, gap: 0.25, distances: new Float32Array([0, 2])}},
    }, () => {});
    assert.ok(seen.flags & InstanceFlag.Unlit && seen.flags & InstanceFlag.NoPick && seen.flags & InstanceFlag.DepthOffset && seen.flags & InstanceFlag.Clipped);
    assert.equal(seen.flags & InstanceFlag.OnTop, 0);
    assert.deepEqual([seen.dash, seen.gap, ...seen.distances], [0.5, 0.25, 0, 2]);

    new NativeResultLayer(module, renderer, {positions, indices: null, topology: "lines", overlay: {onTop: true, clipped: false}}, () => {});
    assert.ok(seen.flags & InstanceFlag.OnTop);
    assert.equal(seen.flags & (InstanceFlag.Clipped | InstanceFlag.DepthOffset), 0);
    assert.equal(seen.dash, 0);
});

/** Where each vertex of a drawn layer is: where it was made plus how far it was moved. */
function placed(d: Drawn): number[] {
    return [...d.data.positions].map((p, i) => p + (d.displacement[i] ?? 0) * (d.displacement.length ? d.influence : 0));
}

test("points replaced with as many or fewer keep their geometry: the rest lie on the last one drawn", () => {
    const {drawn, layer} = fakeScene();
    const handle = layer.addPoints({positions: new Float32Array([0, 0, 0, 1, 0, 0, 2, 0, 0]), color: [1, 0, 0]});
    layer.setGeometry(handle, new Float32Array([5, 5, 5, 6, 6, 6, 7, 7, 7]));
    assert.equal(drawn.length, 1);
    assert.deepEqual(placed(drawn[0]), [5, 5, 5, 6, 6, 6, 7, 7, 7]);
    // Two of the three, read from the start of a longer buffer.
    layer.setGeometry(handle, new Float32Array([1, 1, 1, 2, 2, 2, 9, 9, 9]), 2);
    assert.equal(drawn.length, 1, "no geometry is made for a smaller count");
    assert.deepEqual(placed(drawn[0]), [1, 1, 1, 2, 2, 2, 2, 2, 2]);
    assert.equal(drawn[0].visible, true);
    // None: nothing of it is drawn, and the geometry is kept for the next ones.
    layer.setGeometry(handle, new Float32Array(0));
    assert.equal(drawn.length, 1);
    assert.equal(drawn[0].visible, false);
    layer.setGeometry(handle, new Float32Array([3, 3, 3]));
    assert.equal(drawn[0].visible, true);
    assert.deepEqual(placed(drawn[0]), [3, 3, 3, 3, 3, 3, 3, 3, 3]);
});

test("more points than the geometry holds make one larger geometry, and the old one is removed", () => {
    const {drawn, layer} = fakeScene();
    const handle = layer.addPoints({positions: new Float32Array([0, 0, 0, 1, 0, 0]), colors: new Float32Array([1, 0, 0, 0, 1, 0])});
    const five = Float32Array.from({length: 15}, (_, i) => i);
    const tints = Float32Array.from({length: 15}, (_, i) => i / 15);
    layer.setGeometry(handle, five, 5, tints);
    assert.equal(drawn.length, 2);
    assert.equal(drawn[0].removed, true, "nothing of the old geometry stays on the GPU");
    assert.deepEqual([...drawn[1].data.positions], [...five]);
    assert.deepEqual(drawn[1].colors, [...tints]);
    assert.equal(drawn[1].displacement.length, 0, "made where it is: nothing to move");
    // A count that creeps up does not make a geometry each time: there was room for half as many again.
    layer.setGeometry(handle, Float32Array.from({length: 18}, (_, i) => -i), 6);
    assert.equal(drawn.length, 3);
    const capacity = drawn[2].data.positions.length / 3;
    assert.ok(capacity >= 7, `room for ${capacity}`);
    layer.setGeometry(handle, Float32Array.from({length: 21}, (_, i) => i * 2), 7);
    assert.equal(drawn.length, 3);
    assert.deepEqual(placed(drawn[2]).slice(0, 21), Array.from({length: 21}, (_, i) => i * 2));
});

test("segments replaced with fewer leave the rest without length, and colours follow each segment", () => {
    const {drawn, layer} = fakeScene();
    const handle = layer.addLines({
        segments: new Float32Array([0, 0, 0, 1, 0, 0, 1, 0, 0, 2, 0, 0, 2, 0, 0, 3, 0, 0]),
        colors: new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1]),
    });
    layer.setGeometry(handle, new Float32Array([0, 0, 1, 0, 0, 2]), 1, new Float32Array([0.5, 0.5, 0.5]));
    assert.equal(drawn.length, 1);
    assert.deepEqual(placed(drawn[0]), [0, 0, 1, 0, 0, 2, 0, 0, 2, 0, 0, 2, 0, 0, 2, 0, 0, 2]);
    assert.deepEqual(drawn[0].colors.slice(0, 6), [0.5, 0.5, 0.5, 0.5, 0.5, 0.5]);
});

test("dashed lines are measured again when they are replaced", () => {
    const {drawn, layer} = fakeScene();
    const handle = layer.addLines({segments: new Float32Array([0, 0, 0, 1, 0, 0]), dashed: {dash: 0.2, gap: 0.1}});
    layer.setGeometry(handle, new Float32Array([0, 0, 0, 0, 3, 4]));
    assert.equal(drawn.length, 2);
    assert.deepEqual([...drawn[1].data.overlay!.dashed!.distances], [0, 5]);
});

test("a look is changed in place and kept for when the overlay is made again", () => {
    const {scene, drawn, layer} = fakeScene();
    const handle = layer.addPoints({positions: new Float32Array([1, 2, 3]), color: [1, 0, 0]});
    const matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 4, 5, 6, 1];
    layer.setLook(handle, {visible: false, opacity: 0.25, color: [0, 1, 0], matrix});
    assert.equal(drawn.length, 1, "no geometry is made for a look");
    assert.equal(drawn[0].visible, false);
    assert.equal(drawn[0].opacity, 0.25);
    assert.deepEqual(drawn[0].color, [0, 1, 0]);
    assert.deepEqual(drawn[0].matrix, matrix);
    // Only what is named changes.
    layer.setLook(handle, {visible: true});
    assert.equal(drawn[0].visible, true);
    assert.equal(drawn[0].opacity, 0.25);
    layer.setLook(handle, {visible: false});
    scene.sceneEpoch++;
    for (const listener of scene.onLoaded) listener();
    assert.equal(drawn.length, 2);
    assert.equal(drawn[1].visible, false);
    assert.equal(drawn[1].data.opacity, 0.25);
    assert.deepEqual(drawn[1].color, [0, 1, 0]);
    assert.deepEqual(drawn[1].data.matrix, matrix);
});
