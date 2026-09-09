import assert from "node:assert/strict";
import {describe, test} from "node:test";

import {segmentRangeIds, selectedSegments} from "@/utils/scene/fea/lineSegmentIds";

// The accumulation these mirror, from applyElemField: for each line element,
// push a vertex per endpoint whose value is finite, then drop a lone vertex so
// every element contributes a complete pair or nothing.
function accumulate(elements: ReadonlyArray<{label: number; finite: [boolean, boolean]}>) {
    const vertexLabels: number[] = [];
    const positions: number[] = [];
    for (const {label, finite} of elements) {
        for (let endpoint = 0; endpoint < 2; endpoint++) {
            if (!finite[endpoint]) continue;
            // A position that identifies the element AND the end it came from,
            // so a mis-pairing is visible in the coordinates.
            positions.push(label + endpoint / 10, 0, 0);
            vertexLabels.push(label);
        }
        if (vertexLabels.length % 2) {
            vertexLabels.splice(-1, 1);
            positions.splice(-3, 3);
        }
    }
    return {vertexLabels, positions};
}

describe("which element each segment belongs to", () => {
    test("one segment per element, in order", () => {
        const {vertexLabels} = accumulate([
            {label: 363, finite: [true, true]},
            {label: 364, finite: [true, true]},
            {label: 365, finite: [true, true]},
        ]);
        assert.deepEqual(segmentRangeIds(vertexLabels), ["E363", "E364", "E365"]);
    });

    test("an element that emitted nothing takes no segment", () => {
        const {vertexLabels} = accumulate([
            {label: 363, finite: [true, true]},
            {label: 364, finite: [false, false]},
            {label: 365, finite: [true, true]},
        ]);
        assert.deepEqual(segmentRangeIds(vertexLabels), ["E363", "E365"]);
    });

    test("A LONE ENDPOINT DOES NOT SHIFT THE REST", () => {
        // This is the case the first attempt got wrong: element 364 contributes
        // one finite end, which is dropped. If the ids and the vertices drift by
        // one here, the highlight names 365 and draws a line from 364's end to
        // 365's start — a segment spanning two elements, which is exactly the
        // stray line that was seen.
        const {vertexLabels, positions} = accumulate([
            {label: 363, finite: [true, true]},
            {label: 364, finite: [true, false]},
            {label: 365, finite: [true, true]},
        ]);
        const ids = segmentRangeIds(vertexLabels);
        assert.deepEqual(ids, ["E363", "E365"]);

        // And the geometry agrees: segment 1 is 365's two ends, not a splice of
        // 364's and 365's.
        const segment = selectedSegments(ids, ["E365"])[0];
        assert.equal(segment, 1);
        assert.deepEqual(positions.slice(segment * 6, segment * 6 + 6), [365, 0, 0, 365.1, 0, 0]);
    });

    test("every segment's two ends come from the same element", () => {
        const elements = [
            {label: 1, finite: [true, true] as [boolean, boolean]},
            {label: 2, finite: [false, true] as [boolean, boolean]},
            {label: 3, finite: [true, true] as [boolean, boolean]},
            {label: 4, finite: [true, false] as [boolean, boolean]},
            {label: 5, finite: [true, true] as [boolean, boolean]},
        ];
        const {vertexLabels, positions} = accumulate(elements);
        const ids = segmentRangeIds(vertexLabels);
        for (let s = 0; s < ids.length; s++) {
            const label = Number(ids[s].slice(1));
            assert.equal(positions[s * 6], label, `segment ${s} start`);
            assert.equal(positions[s * 6 + 3], label + 0.1, `segment ${s} end`);
        }
    });

    test("refuses to guess when a pair straddles two elements", () => {
        // Not reachable through `accumulate`; it is the invariant that made the
        // first version fail silently, so it fails loudly instead.
        assert.throws(() => segmentRangeIds([363, 364]), /mis-paired/);
    });

    test("refuses an incomplete pair", () => {
        assert.throws(() => segmentRangeIds([363, 363, 364]), /incomplete pair/);
    });

    test("no labels, no segments", () => {
        assert.deepEqual(segmentRangeIds([]), []);
    });
});

describe("picking the selected segments", () => {
    const ids = ["E1", "E2", "E3", "E2"];

    test("finds every segment of a selected element", () => {
        assert.deepEqual(selectedSegments(ids, ["E2"]), [1, 3]);
    });

    test("handles several selections", () => {
        assert.deepEqual(selectedSegments(ids, ["E3", "E1"]), [0, 2]);
    });

    test("an empty or unmatched selection picks nothing", () => {
        assert.deepEqual(selectedSegments(ids, []), []);
        assert.deepEqual(selectedSegments(ids, ["E99"]), []);
    });
});
