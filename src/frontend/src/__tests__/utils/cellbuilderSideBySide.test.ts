import assert from "node:assert/strict";
import {test} from "node:test";

import {sideBySideOffsetX} from "../../utils/cellbuilder/sideBySide";

// The clearance the two same-centred copies need to not overlap: a result
// shifted by S starts at (base - Wr/2 + S) and must sit past the topology's far
// edge (base + Wt/2). So S must exceed (Wt + Wr)/2 for any margin > 0.
function clears(topologyWidth: number, resultWidth: number): boolean {
    const s = sideBySideOffsetX(topologyWidth, resultWidth);
    return s > (topologyWidth + resultWidth) / 2;
}

test("equal-width copies clear each other", () => {
    assert.equal(clears(20, 20), true);
});

test("wide topology + narrow result still clears (result-only width would overlap)", () => {
    // The reported bug: a result narrower than the topology (or, at the extreme,
    // an unmeasured 0-width result) offset by only the result width overlaps.
    assert.equal(clears(30, 2), true);
    assert.equal(sideBySideOffsetX(30, 2) > 30, true); // past the topology's far half
});

test("wide result + narrow topology clears too", () => {
    assert.equal(clears(2, 30), true);
});

test("unmeasured result (0 width) falls back to the topology width", () => {
    // The personal-scope demo case: the freshly-loaded result group measured 0.
    // The offset must still clear the topology using the known topology width.
    const s = sideBySideOffsetX(20, 0);
    assert.equal(s > 20, true);
    assert.equal(clears(20, 0), true);
});

test("non-finite widths never produce 0/NaN (a floored positive shift)", () => {
    for (const bad of [NaN, Infinity, -Infinity, -5]) {
        const s = sideBySideOffsetX(bad, bad);
        assert.equal(Number.isFinite(s), true);
        assert.equal(s > 0, true);
    }
    // Both zero → still a positive 1 m aisle so the copies never coincide.
    assert.equal(sideBySideOffsetX(0, 0) >= 1, true);
});

test("shift scales with model size (a translated/large model keeps a real gap)", () => {
    // Independent of any base translation (the caller adds baseX), the shift
    // must grow with the model so a big model doesn't get a hairline gap.
    assert.equal(sideBySideOffsetX(100, 100) > sideBySideOffsetX(10, 10), true);
});
