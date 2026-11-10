// Number labels placed from plain data: a result as a label source, the camera as a projector.
import assert from "node:assert/strict";
import {test} from "node:test";

import {matrixProjector, placeLabels, type LabelSource, type ScreenProjector} from "@/utils/scene/fea/labelPlacement";

/** Two triangles sharing an edge: elements E1 (nodes 0,1,2) and E2 (nodes 1,2,3). */
const positions = [-2, -1, 0, 2, -1, 0, 0, 1, 0, 3, 3, 0];
function source(over: Partial<LabelSource> = {}): LabelSource {
    return {
        ranges: new Map([["E1", [0, 3]], ["E2", [3, 3]]]),
        index: [0, 1, 2, 1, 2, 3],
        position: (v, out, at) => {
            out.set(positions.slice(v * 3, v * 3 + 3), at);
            return true;
        },
        ...over,
    };
}

/** A view of 800 x 600 that shows x in -4..4 and y in -3..3, looking down z. */
const flat: ScreenProjector = (world, count, out) => {
    for (let i = 0; i < count; i++) {
        out[i * 3] = (world[i * 3] + 4) * 100;
        out[i * 3 + 1] = (3 - world[i * 3 + 1]) * 100;
        out[i * 3 + 2] = 0.5;
    }
    return {width: 800, height: 600};
};
const OPTS = {nodes: true, elements: false, cap: 100, scanCap: 100};

test("node labels: each node once, where the projector puts it, numbered from 1 unless told otherwise", () => {
    const out = placeLabels(source(), flat, OPTS);
    assert.deepEqual(out.map((l) => l.text).sort(), ["1", "2", "3", "4"]);
    const first = out.find((l) => l.text === "1")!;
    assert.deepEqual([first.x, first.y, first.kind, first.key], [200, 400, "node", "n:0"]);
    const named = placeLabels(source(), flat, {...OPTS, nodeText: (i) => String(101 + i)});
    assert.deepEqual(named.map((l) => l.text).sort(), ["101", "102", "103", "104"]);
});

test("a mesh with a vertex per triangle corner labels each node once, by the node's number", () => {
    // Element-local: six vertices drawn in order, each knowing its node.
    const nodes = [0, 1, 2, 1, 2, 3];
    const local = source({
        index: [0, 1, 2, 3, 4, 5],
        nodeOf: (v) => nodes[v],
        position: (v, out, at) => {
            out.set(positions.slice(nodes[v] * 3, nodes[v] * 3 + 3), at);
            return true;
        },
    });
    const out = placeLabels(local, flat, OPTS);
    assert.deepEqual(out.map((l) => l.text).sort(), ["1", "2", "3", "4"]);
});

test("element labels sit at the middle of the element; a value takes the place of the number", () => {
    const out = placeLabels(source(), flat, {...OPTS, nodes: false, elements: true});
    assert.deepEqual(out.map((l) => [l.text, Math.round(l.x), Math.round(l.y)]), [["1", 400, 333], ["2", 567, 200]]);
    const values = placeLabels(source(), flat, {...OPTS, nodes: false, elements: true, values: true, valueText: (n) => (n === 2 ? "9.5" : undefined)});
    assert.deepEqual(values.map((l) => [l.kind, l.text]), [["value", "9.5"], ["element", "1"]]);
});

test("a selection narrows the labels to its elements, and hidden elements get none", () => {
    const only = placeLabels(source(), flat, {...OPTS, nodes: false, elements: true, selected: new Set(["E2"])});
    assert.deepEqual(only.map((l) => l.text), ["2"]);
    const rest = placeLabels(source({isHidden: (key) => key === "E1"}), flat, {...OPTS, nodes: false, elements: true});
    assert.deepEqual(rest.map((l) => l.text), ["2"]);
});

test("what is off screen or behind the camera gets no label, and one cell of the grid holds one", () => {
    const behind: ScreenProjector = (world, count, out) => {
        flat(world, count, out);
        out[0] = out[1] = out[2] = NaN; // the first point is behind the camera
        out[5] = 1.5; // the second is beyond the far plane
        return {width: 800, height: 600};
    };
    assert.deepEqual(placeLabels(source(), behind, OPTS).map((l) => l.text).sort(), ["3", "4"]);
    const narrow: ScreenProjector = (world, count, out) => ({...flat(world, count, out), width: 450});
    assert.deepEqual(placeLabels(source(), narrow, OPTS).map((l) => l.text).sort(), ["1", "3"], "x beyond the view's width");
    const coarse = placeLabels(source(), flat, {...OPTS, cellW: 800, cellH: 600});
    assert.equal(coarse.length, 1);
    assert.equal(placeLabels(source(), flat, {...OPTS, cap: 2}).length, 2);
    assert.deepEqual(placeLabels(null, flat, OPTS), []);
    assert.deepEqual(placeLabels(source(), flat, {...OPTS, nodes: false}), []);
});

test("a projector from a camera's matrices: pixels from the top-left, depth from near to far", () => {
    const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    // A projection that halves x and y and leaves z: clip space is the cube itself.
    const proj = [0.5, 0, 0, 0, 0, 0.5, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    const out = new Float32Array(6);
    const size = matrixProjector(identity, proj, 800, 600, -1)(new Float32Array([0, 0, 0, 2, 2, -1]), 2, out);
    assert.deepEqual(size, {width: 800, height: 600});
    assert.deepEqual([...out], [400, 300, 0.5, 800, 0, 0]);
    // The other convention: clip z runs from 0.
    matrixProjector(identity, proj, 800, 600, 0)(new Float32Array([0, 0, 0.25, 0, 0, 0]), 2, out);
    assert.deepEqual([out[2], out[5]], [0.25, 0]);
    // Behind the camera (w not positive): nothing to place.
    const perspective = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1, 0, 0, 0, 0];
    matrixProjector(identity, perspective, 800, 600, -1)(new Float32Array([0, 0, 1]), 1, out);
    assert.ok(Number.isNaN(out[0]));
});
