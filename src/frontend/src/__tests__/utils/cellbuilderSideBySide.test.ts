import assert from "node:assert/strict";
import {test} from "node:test";

import {MIN_SIDE_BY_SIDE_GAP, sideBySideOffsetX} from "../../utils/cellbuilder/sideBySide";

// The side-by-side view seats the compiled result beside the editable topology.
//
// This used to be expressed in WIDTHS — max(Wt, Wr) + gap — with the comment
// "the clearance two same-centred copies need". That holds only while both
// copies are centred on the same point, and they are not: measured on a real
// model ("demo", personal scope) the cell topology runs X 0 → 10, authored from
// the origin outward, while a compiled GLB may be centred on it. The clearance
// actually needed is then topologyMax − resultMin, which no width-only formula
// can express — and the result kept overlapping by roughly a third of a model.
//
// So the arguments are now SPANS in the shared base frame. Every case below is
// stated as "where does the result's left edge land", which is the only thing
// that decides whether the two overlap, and is checked for BOTH layouts.

/** Left edge of the result after shifting. */
const leftEdge = (shift: number, resultMinX: number) => shift + resultMinX;

/** Centred layout: both copies straddle the origin. */
function clearsCentred(wt: number, wr: number): boolean {
    const topologyMaxX = wt / 2;
    const resultMinX = -wr / 2;
    const s = sideBySideOffsetX(topologyMaxX, resultMinX, Math.max(wt, wr));
    return leftEdge(s, resultMinX) >= topologyMaxX;
}

/** Origin-based layout: both copies run 0 → width. */
function clearsFromOrigin(wt: number, wr: number): boolean {
    const s = sideBySideOffsetX(wt, 0, Math.max(wt, wr));
    return leftEdge(s, 0) >= wt;
}

test("equal-width copies clear each other", () => {
    assert.equal(clearsCentred(20, 20), true);
    assert.equal(clearsFromOrigin(20, 20), true);
});

test("wide topology + narrow result still clears", () => {
    // A result narrower than the topology must still be pushed past the
    // topology's far edge, not merely past its own width.
    assert.equal(clearsCentred(30, 2), true);
    assert.equal(clearsFromOrigin(30, 2), true);
});

test("wide result + narrow topology clears too", () => {
    assert.equal(clearsCentred(2, 30), true);
    assert.equal(clearsFromOrigin(2, 30), true);
});

test("an origin-based topology and a CENTRED result clear — the reported overlap", () => {
    // topology 0 → 10, result centred -5 → +5. This is the case the width
    // formula could not separate.
    const shift = sideBySideOffsetX(10, -5, 10);
    assert.ok(leftEdge(shift, -5) >= 10, `left edge ${leftEdge(shift, -5)} vs topology end 10`);
});

test("the previous width-only rule would have overlapped here", () => {
    // max(10,10) + 0.15*10 = 11.5 -> a centred result starts at 6.5, which is
    // 3.5 m inside a topology ending at 10. Kept as the regression witness.
    const oldShift = Math.max(10, 10) + Math.max(10 * 0.15, 1);
    assert.ok(leftEdge(oldShift, -5) < 10);
});

test("a topology that does not start at zero is handled", () => {
    // Width 5 but far edge 25 — the divergence a width formula cannot see.
    const shift = sideBySideOffsetX(25, 0, 5);
    assert.ok(leftEdge(shift, 0) >= 25);
});

test("unmeasured result (0 width) still separates", () => {
    // The freshly-loaded group can measure 0; it must still move clear.
    const shift = sideBySideOffsetX(20, 0, 20);
    assert.ok(leftEdge(shift, 0) >= 20);
});

test("non-finite measurements never produce 0/NaN", () => {
    for (const bad of [NaN, Infinity, -Infinity]) {
        const s = sideBySideOffsetX(bad, bad, bad);
        assert.equal(Number.isFinite(s) && s > 0, true, `bad input ${bad} yielded ${s}`);
    }
});

test("an empty model still gets the floor gap", () => {
    assert.equal(sideBySideOffsetX(0, 0, 0) >= MIN_SIDE_BY_SIDE_GAP, true);
});

test("the shift never pulls the result backwards", () => {
    // Empty topology, result already to the right: a negative shift would drag
    // it back across the origin.
    assert.ok(sideBySideOffsetX(0, 50, 10) > 0);
});

test("shift scales with model size", () => {
    assert.ok(sideBySideOffsetX(100, -50, 100) > sideBySideOffsetX(10, -5, 10));
});

test("it is idempotent given a stable measurement", () => {
    assert.equal(sideBySideOffsetX(10, -5, 10), sideBySideOffsetX(10, -5, 10));
});
