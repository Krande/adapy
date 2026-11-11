// One step of an element field as plain numbers: the colours per render vertex, the displacement
// per node, the result-point markers and the coloured lines of line elements. No renderer.
import assert from "node:assert/strict";
import {test} from "node:test";

import type {FeaManifestField} from "@/services/viewerApi";
import {getColormap, linearColormap} from "@/utils/scene/fea/colormaps";
import {elementLocalVertices, expandTriplesInto, paintElemField, type ElemFieldPaint} from "@/utils/scene/fea/elemFieldPaint";

/** Four nodes, two triangles that share the nodes 1 and 2; element 7 then element 8. */
const basePositions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]);
const sourceIndices = [0, 1, 2, 1, 3, 2];
const drawRanges = new Map<string, [number, number]>([["E7", [0, 3]], ["E8", [3, 3]]]);

function field(over: Record<string, unknown> = {}, bucket: Record<string, unknown> = {}): FeaManifestField {
    return {
        name_canonical: "stress", name_native: "S", kind: "scalar", category: "stress", support: "element_average",
        analysis_kind: "static", components: ["S"], n_steps: 1, steps: [{i: 0, value: 1, label: "1"}],
        scalar_range: {S: [0, 4]}, default_view: {reduction: "S", colormap: "viridis"},
        per_type: [{
            elem_type: "triangle", n_elements: 2, n_ips: 1, ip_layout: [], element_labels: [7, 8],
            blob: {url: "x", header_bytes: 0, stride_bytes: 0, dtype: "float32", byte_order: "little"},
            scalar_range: {S: [0, 4]}, ...bucket,
        }],
        ...over,
    } as unknown as FeaManifestField;
}

// What a kernel hands a viewport is linear light: the colour map's sRGB decoded (colormaps.ts).
function colorAt(t: number): number[] {
    const out = new Float32Array(3);
    linearColormap(getColormap("viridis"))(t, out, 0);
    return [...out];
}

const local = elementLocalVertices(sourceIndices);
const paint = (over: Partial<ElemFieldPaint>, nRender = local.renderToSource.length) => {
    const colors = new Float32Array(nRender * 3).fill(-1);
    const painted = paintElemField(
        {
            basePositions, renderToSource: local.renderToSource, indices: local.indices, drawRanges, colorField: field(),
            perTypeStepValues: [new Float32Array([1, 3])], layer: "all", ipReduction: "mean", reduction: "S", colormap: "viridis",
            ...over,
        },
        colors,
    );
    return {colors: [...colors], painted};
};
const rgb = (colors: number[], vertex: number) => colors.slice(vertex * 3, vertex * 3 + 3);

test("element-local vertices: one per triangle corner, drawn in order, each knowing its node", () => {
    assert.deepEqual([...local.renderToSource], sourceIndices);
    assert.deepEqual([...local.indices], [0, 1, 2, 3, 4, 5]);
    const expanded = new Float32Array(18);
    expandTriplesInto(basePositions, local.renderToSource, expanded);
    assert.deepEqual([...expanded.slice(9, 12)], [1, 0, 0], "corner 3 is node 1");
});

test("each element is painted in its own colour, shared nodes included", () => {
    const {colors, painted} = paint({});
    for (const v of [0, 1, 2]) assert.deepEqual(rgb(colors, v), colorAt(0.25));
    for (const v of [3, 4, 5]) assert.deepEqual(rgb(colors, v), colorAt(0.75));
    assert.deepEqual([...painted.values], [["E7", 1], ["E8", 3]]);
    assert.deepEqual([...painted.sourceDisplacement], new Array(12).fill(0), "no warp field: undeformed");
    assert.equal(painted.markers.positions.length, 0);
    assert.equal(painted.lines.positions.length, 0);
});

test("the range the scale follows can be handed in, and an element without a value stays grey", () => {
    const {colors, painted} = paint({autoRange: [1, 3], perTypeStepValues: [new Float32Array([1, NaN])]});
    assert.deepEqual(rgb(colors, 0), colorAt(0));
    assert.deepEqual(rgb(colors, 4), [0.5, 0.5, 0.5]);
    assert.deepEqual([...painted.values], [["E7", 1]]);
});

test("averaged onto the nodes, a shared node carries the mean of its elements", () => {
    const identity = Uint32Array.from([0, 1, 2, 3]);
    const {colors} = paint({renderToSource: identity, indices: sourceIndices, nodalAverage: true}, 4);
    assert.deepEqual(rgb(colors, 0), colorAt(0.25));
    assert.deepEqual(rgb(colors, 1), colorAt(0.5));
    assert.deepEqual(rgb(colors, 2), colorAt(0.5));
    assert.deepEqual(rgb(colors, 3), colorAt(0.75));
});

test("the displacement is the warp field's translation, per node", () => {
    const warpField = {components: ["ALL", "X", "Y", "Z"], name_canonical: "displacement"} as unknown as FeaManifestField;
    const warpStepValues = new Float32Array([9, 1, 2, 3, 9, 0, 0, 0, 9, 0, 0, 0, 9, 4, 5, 6]);
    const {painted} = paint({warpField, warpStepValues});
    assert.deepEqual([...painted.sourceDisplacement], [1, 2, 3, 0, 0, 0, 0, 0, 0, 4, 5, 6]);
    assert.throws(() => paint({warpField, warpStepValues: new Float32Array(3)}), /warpStepValues length/);
});

test("a field with result points gives a marker per point: where it is, its colour and how it moves", () => {
    const warpField = {components: ["X", "Y", "Z"], name_canonical: "displacement"} as unknown as FeaManifestField;
    const warpStepValues = new Float32Array([0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
    const points = field({support: "result_point"}, {element_node_indices: [[0, 1, 2], [1, 3, 2]]});
    const {painted} = paint({colorField: points, warpField, warpStepValues});
    const round = (a: Float32Array) => [...a].map((v) => Math.round(v * 1000) / 1000);
    assert.deepEqual(round(painted.markers.positions), [0.333, 0.333, 0, 0.667, 0.667, 0]);
    assert.deepEqual([...painted.markers.colors], [...colorAt(0.25), ...colorAt(0.75)]);
    assert.deepEqual(round(painted.markers.displacement), [0, 0, 1, 0, 0, 0], "node 0 is a third of the first point");
    // A mesh that only repeats another's elements draws none.
    assert.equal(paint({colorField: points, resultPoints: false}).painted.markers.positions.length, 0);
});

test("a line element is drawn as a coloured line between its end nodes", () => {
    const lines = field({}, {elem_type: "line", element_labels: [20, 21], element_node_indices: [[0, 1], [1, 3]]});
    const {painted, colors} = paint({colorField: lines});
    assert.deepEqual([...painted.lines.positions], [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 0]);
    assert.deepEqual([...painted.lines.colors], [...colorAt(0.25), ...colorAt(0.25), ...colorAt(0.75), ...colorAt(0.75)]);
    assert.deepEqual(painted.lineVertexLabels, [20, 20, 21, 21]);
    assert.ok(colors.every((c) => c === 0.5), "no face is painted by a line element");
    assert.equal(paint({colorField: lines, lineFallback: false}).painted.lines.positions.length, 0);
});

test("a step of the wrong length throws before the colours on screen are touched", () => {
    const colors = new Float32Array(18).fill(-1);
    assert.throws(
        () => paintElemField(
            {
                basePositions, renderToSource: local.renderToSource, indices: local.indices, drawRanges, colorField: field(),
                perTypeStepValues: [new Float32Array(5)], layer: "all", ipReduction: "mean", reduction: "S",
            },
            colors,
        ),
        /step view length 5 != expected 2/,
    );
    assert.ok(colors.every((c) => c === -1));
});
