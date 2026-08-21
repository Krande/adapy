import assert from "node:assert/strict";
import {test} from "node:test";

import {visibleEdgeIndex} from "../../shell/feaEdgeFilter";

// Two quads side by side, sharing the edge 1–2.
//
//   0───1───4
//   │ A │ B │
//   3───2───5
//
// Each quad is two triangles, so each owns 6 entries of the triangle index buffer.
const TRI = [
    0, 1, 2, 0, 2, 3, // E1 (quad A)
    1, 4, 5, 1, 5, 2, // E2 (quad B)
];
const RANGES = new Map<string, [number, number]>([
    ["E1", [0, 6]],
    ["E2", [6, 6]],
]);
// The overlay: real element boundaries, deduped, no diagonals.
const EDGES = [0, 1, 1, 2, 2, 3, 3, 0, 1, 4, 4, 5, 5, 2];

const pairs = (a: ArrayLike<number> | null) => {
    if (!a) return null;
    const out: string[] = [];
    for (let i = 0; i + 1 < a.length; i += 2) out.push(`${a[i]}-${a[i + 1]}`);
    return out;
};

test("isolating one element keeps that element's own boundary", () => {
    // The whole point of the fix: an isolated group has to keep its mesh lines, or it
    // renders as a featureless solid.
    const got = pairs(visibleEdgeIndex(TRI, RANGES, new Set(["E1"]), EDGES));
    assert.deepEqual(got, ["0-1", "1-2", "2-3", "3-0"]);
});

test("the shared edge survives, drawn from whichever side is visible", () => {
    // 1–2 belongs to both quads. Isolating B must still outline B fully — dropping the
    // shared edge would leave the cut face open.
    const got = pairs(visibleEdgeIndex(TRI, RANGES, new Set(["E2"]), EDGES));
    assert.ok(got);
    assert.ok(got.includes("1-2"), "cut-face edge must stay drawn");
    assert.deepEqual(got, ["1-2", "1-4", "4-5", "5-2"]);
});

test("edges of hidden elements are dropped", () => {
    const got = pairs(visibleEdgeIndex(TRI, RANGES, new Set(["E1"]), EDGES));
    assert.ok(got);
    for (const hidden of ["1-4", "4-5", "5-2"]) {
        assert.ok(!got.includes(hidden), `${hidden} belongs only to the hidden element`);
    }
});

test("selecting everything reproduces the full edge list", () => {
    const got = pairs(visibleEdgeIndex(TRI, RANGES, new Set(["E1", "E2"]), EDGES));
    assert.deepEqual(got, pairs(EDGES));
});

test("ids that name no draw range decline rather than blank the wireframe", () => {
    // Node members (P{n}) and groups from another model land here. Filtering to nothing
    // would erase every line, so the caller is told to leave the overlay alone.
    assert.equal(visibleEdgeIndex(TRI, RANGES, new Set(["P1", "P2"]), EDGES), null);
    assert.equal(visibleEdgeIndex(TRI, RANGES, new Set(), EDGES), null);
});

test("nothing to work with is declined, not guessed at", () => {
    assert.equal(visibleEdgeIndex(null, RANGES, new Set(["E1"]), EDGES), null);
    assert.equal(visibleEdgeIndex(TRI, RANGES, new Set(["E1"]), []), null);
});

test("a range that is not a whole number of triangles does not read into its neighbour", () => {
    // A truncated count used to walk past the element's own vertices and mark edges
    // belonging to something hidden — the kind of wrong that only shows up as a stray
    // line nobody can account for.
    const ranges = new Map<string, [number, number]>([["E1", [0, 5]]]);
    const got = pairs(visibleEdgeIndex(TRI, ranges, new Set(["E1"]), EDGES));
    assert.deepEqual(got, ["0-1", "1-2"], "only the one complete triangle contributes");
});

test("vertex indices well past 16 bits still key distinctly", () => {
    // The key packs two indices into one number; a shift too small would collide and
    // silently keep edges from hidden elements.
    const big = [1_000_000, 2_000_000, 3_000_000];
    const tri = [...big];
    const ranges = new Map<string, [number, number]>([["E1", [0, 3]]]);
    const edges = [1_000_000, 2_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000];
    const got = pairs(visibleEdgeIndex(tri, ranges, new Set(["E1"]), edges));
    assert.deepEqual(got, ["1000000-2000000", "2000000-3000000"]);
});
