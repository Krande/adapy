// The numbers both viewports fade an element grid by, and a line found under a point of the view.
import assert from "node:assert/strict";
import {test} from "node:test";

import {edgeFadeOpacity, pixelsPerUnitAtFocus, typicalEdgeLength} from "@/utils/scene/fea/edgeFade";
import {inFrontOf, lineAt} from "@/utils/scene/fea/streaming/layerLinePick";

test("edges fade from full at the setting to gone at three eighths of it", () => {
    assert.equal(edgeFadeOpacity(8, 8), 1);
    assert.equal(edgeFadeOpacity(20, 8), 1);
    assert.equal(edgeFadeOpacity(3, 8), 0);
    assert.equal(edgeFadeOpacity(1, 8), 0);
    assert.equal(edgeFadeOpacity(5.5, 8), 0.5);
});

test("a grid's spacing is its typical edge, not its longest", () => {
    const positions = [0, 0, 0, 1, 0, 0, 2, 0, 0, 3, 0, 0, 3, 40, 0];
    assert.equal(typicalEdgeLength(positions, new Uint32Array([0, 1, 1, 2, 2, 3, 3, 4])), 1);
    assert.equal(typicalEdgeLength(positions, new Uint32Array(0)), 0);
});

test("the density is taken where the user looks, inside the model's box", () => {
    const box = [-1, -1, -1, 1, 1, 1];
    // 10 away from the point looked at, 90 degrees, 1000 pixels high: 20 units fill the height.
    assert.equal(Math.round(pixelsPerUnitAtFocus([0, 0, 10], [0, 0, 0], box, 90, 1000)), 50);
    // A target outside the box counts as the nearest point of the box: 9 away now.
    assert.equal(Math.round(pixelsPerUnitAtFocus([0, 0, 10], [0, 0, 50], box, 90, 1000) * 18), 1000);
});

/** Looking down -z at 10 pixels per unit; depth 0.5 at z = 0, nearer for larger z. */
const project = (world: Float32Array, count: number, out: Float32Array) => {
    for (let i = 0; i < count * 3; i += 3) {
        out[i] = world[i] * 10;
        out[i + 1] = world[i + 1] * 10;
        out[i + 2] = 0.5 - world[i + 2] * 0.01;
    }
};

test("the line under the cursor is the nearest one within reach, where it is drawn now", () => {
    const lines = {
        // Two lines along x: one at z = 0, one above it at z = 5.
        positions: new Float32Array([0, 0, 0, 10, 0, 0, 0, 0, 5, 10, 0, 5]),
        displacement: new Float32Array([0, 2, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0]),
        influence: 0,
    };
    const hit = lineAt(lines, project, 50, 3)!;
    assert.equal(hit.line, 1);
    assert.equal(+hit.depth.toFixed(4), 0.45);
    assert.equal(lineAt(lines, project, 50, 6), null);
    assert.equal(lineAt(lines, project, 110, 0), null, "past its end");
    // The nearer one hidden: the one behind it is clicked.
    assert.equal(lineAt({...lines, skip: (line) => line === 1}, project, 50, 0)?.line, 0);
    // Displaced, the first line is drawn 20 pixels up.
    assert.equal(lineAt({...lines, influence: 1}, project, 50, 20)?.line, 0);
    // Placed in the world by a matrix.
    const moved = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 3, 0, 1];
    assert.equal(lineAt({...lines, matrix: moved}, project, 50, 0), null);
    assert.equal(lineAt({...lines, matrix: moved}, project, 50, 30)?.line, 1);
});

test("a line on a surface counts as in front of it, one behind does not", () => {
    assert.equal(inFrontOf(0.4, 0.5), true);
    assert.equal(inFrontOf(0.5, 0.5), true);
    assert.equal(inFrontOf(0.5005, 0.5), true);
    assert.equal(inFrontOf(0.51, 0.5), false);
});
