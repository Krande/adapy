// The coloured lines of line elements on a result drawn through result layers: which of them are
// drawn, the lines as objects of the viewport, and their part of a recorded frame.
import assert from "node:assert/strict";
import {test} from "node:test";

import type {PaintedPoints} from "@/utils/scene/fea/elemFieldPaint";
import {LayerFrameRecorder} from "@/utils/scene/fea/streaming/layerFrames";
import {PaintedPointsLayer, ResultGroup} from "@/utils/scene/fea/streaming/layerGroup";
import {elementLabels, lineObjects, linesWithout} from "@/utils/scene/fea/streaming/layerLines";
import type {ResultMeshLayer, ResultMeshObjects} from "@/viewport/api/resultLayer";

/** Three line elements (5, 6, 7), two points each, every point's numbers its own index. */
function threeLines(): {lines: PaintedPoints; labels: number[]} {
    const each = (scale: number) => Float32Array.from({length: 18}, (_, i) => Math.floor(i / 3) * scale);
    return {lines: {positions: each(1), colors: each(0.1), displacement: each(10)}, labels: [5, 5, 6, 6, 7, 7]};
}

test("element ids are read as labels; what is no element id is left out", () => {
    assert.deepEqual([...elementLabels(["E12", "E7", "P3", "Ex"])], [12, 7]);
});

test("the lines of dropped elements are taken out, and the rest keep their order", () => {
    const {lines, labels} = threeLines();
    const kept = linesWithout(lines, labels, new Set([6]));
    assert.deepEqual([...kept.labels!], [5, 5, 7, 7]);
    assert.deepEqual([...kept.positions], [0, 0, 0, 1, 1, 1, 4, 4, 4, 5, 5, 5]);
    assert.deepEqual([...kept.displacement], [0, 0, 0, 10, 10, 10, 40, 40, 40, 50, 50, 50]);
    assert.equal(kept.colors.length, 12);

    const all = threeLines();
    const same = linesWithout(all.lines, all.labels, null);
    assert.equal(same.positions, all.lines.positions, "nothing to drop: the paint's own arrays");
    assert.deepEqual([...same.labels!], [5, 5, 6, 6, 7, 7]);
    assert.equal(linesWithout(all.lines, all.labels, new Set([5, 6, 7])).positions.length, 0);
});

test("each line is the object its element is", () => {
    const model: ResultMeshObjects = {model: "m", ids: ["E1", "E5", "E7"], segments: new Uint32Array(0), objectOf: new Int32Array(0)};
    const at = new Map([[5, 1], [7, 2]]);
    const objects = lineObjects(model, [5, 5, 6, 6, 7, 7], (label) => at.get(label));
    assert.equal(objects.model, "m");
    assert.equal(objects.ids, model.ids);
    // Two points of the layer per line, in order; element 6 is not one of the model's.
    assert.deepEqual([...objects.segments], [0, 2, 2, 2, 4, 2]);
    assert.deepEqual([...objects.objectOf], [1, -1, 2]);
});

interface Made {
    points: PaintedPoints;
    colors: number[];
    removed: boolean;
}

function pointsLayer(): {layer: PaintedPointsLayer; made: Made[]} {
    const made: Made[] = [];
    const layer = new PaintedPointsLayer(new ResultGroup(), (points) => {
        const m: Made = {points, colors: [], removed: false};
        made.push(m);
        return {
            vertexCount: points.positions.length / 3,
            setColors: (rgb) => void (m.colors = [...rgb]),
            setDisplacement: () => {},
            setInfluence: () => {},
            setColorsVisible: () => {},
            setColor: () => {},
            setVisible: () => {},
            remove: () => void (m.removed = true),
        } satisfies ResultMeshLayer;
    });
    return {layer, made};
}

test("points that stay where they were keep their layer; moved ones get a new one before the old goes", () => {
    const {layer, made} = pointsLayer();
    const a = threeLines().lines;
    assert.equal(layer.show(a), true);
    assert.equal(layer.show(threeLines().lines), false, "the same places in another array");
    assert.equal(made.length, 1);

    const moved = threeLines().lines;
    moved.positions[0] = 9;
    assert.equal(layer.show(moved), true);
    assert.equal(made.length, 2);
    assert.equal(made[0].removed, true);
    assert.equal(made[1].removed, false);

    assert.equal(layer.clear(), true);
    assert.equal(layer.clear(), false);
    assert.equal(layer.drawn, false);
});

const FRAME = {displacement: new Float32Array(3), colors: new Float32Array(3), solidColors: null, solidDisplacement: null};
const BUDGET = 1 << 20;

test("a frame's result points and coloured lines are kept with it, and read back as a paint hands them over", () => {
    const first = threeLines();
    const lines: PaintedPoints = {...first.lines, labels: first.labels};
    const rec = new LayerFrameRecorder("k", [1, 1, 0], 4, BUDGET, {markers: null, lines});
    try {
        assert.equal(rec.record(0, {...FRAME, lines}), true);
        // The next frame: the same places in another array, other colours and another displacement.
        const next = threeLines().lines;
        next.colors.fill(1);
        next.displacement.fill(2);
        assert.equal(rec.record(1, {...FRAME, lines: next}), true);

        const back = rec.points("lines", rec.get(1)!)!;
        assert.equal(back.positions, lines.positions, "where the recording's points are");
        assert.equal(back.labels, first.labels);
        assert.deepEqual([...back.colors], new Array(18).fill(1));
        assert.deepEqual([...back.displacement], new Array(18).fill(2));
        const earlier = rec.points("lines", rec.get(0)!)!;
        assert.equal(earlier, back, "one object, filled for each frame");
        assert.deepEqual([...earlier.displacement], [...first.lines.displacement]);
        assert.equal(rec.points("markers", rec.get(0)!), null);
    } finally {
        rec.dispose();
    }
});

test("a frame whose points are elsewhere, or missing, is not kept", () => {
    const lines = threeLines().lines;
    const rec = new LayerFrameRecorder("k", [1, 1, 0], 4, BUDGET, {markers: null, lines});
    try {
        const moved = threeLines().lines;
        moved.positions[4] = 99;
        assert.equal(rec.record(0, {...FRAME, lines: moved}), false);
        assert.equal(rec.record(0, {...FRAME, lines: null}), false);
        assert.equal(rec.record(0, {...FRAME, lines, markers: lines}), false, "points the recording has none of");
        assert.equal(rec.record(0, {...FRAME, lines}), true);
    } finally {
        rec.dispose();
    }
    // A recording without points keeps frames that have none, as before.
    const plain = new LayerFrameRecorder("k", [1, 1, 0], 4, BUDGET);
    try {
        assert.equal(plain.record(0, FRAME), true);
        assert.equal(plain.record(1, {...FRAME, lines}), false);
    } finally {
        plain.dispose();
    }
});

test("the points count towards what a frame takes of the budget", () => {
    // A node and a face vertex: 15 bytes a frame. Six line points more: 15 bytes each.
    const without = new LayerFrameRecorder("k", [1, 1, 0], 1000, 1500);
    const capacity = without.capacity;
    without.dispose();
    const withLines = new LayerFrameRecorder("k", [1, 1, 0], 1000, 1500, {markers: null, lines: threeLines().lines});
    try {
        assert.equal(capacity, 50);
        assert.equal(withLines.capacity, Math.floor(750 / (15 + 6 * 15)));
    } finally {
        withLines.dispose();
    }
});
