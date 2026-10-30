// Hiding FEA elements hides the wireframe edges no visible element borders any more.
import {test} from "node:test";
import assert from "node:assert/strict";

import {edgeOwners, maskHiddenEdges} from "@/utils/scene/fea/feaEdgeFilter";

test("an edge goes when every element it borders is hidden, and comes back with any of them", () => {
    // Two quads sharing the edge 1-4:  0-1-2 / 3-4-5 grid, E1 = (0,1,4,3), E2 = (1,2,5,4).
    const index = [0, 1, 4, 0, 4, 3, 1, 2, 5, 1, 5, 4];
    const ranges = new Map<string, [number, number]>([["E1", [0, 6]], ["E2", [6, 6]]]);
    // Element edges, plus one (6-7) no element owns (a beam drawn only as a line).
    const edges = Uint32Array.from([0, 1, 1, 4, 4, 3, 3, 0, 1, 2, 2, 5, 5, 4, 6, 7]);
    const owned = edgeOwners(ranges, index, 8, edges);
    const shared = 1; // edge 1-4
    assert.deepEqual(
        Array.from(owned.owners.subarray(owned.offsets[shared], owned.offsets[shared + 1])).map((r) => owned.ids[r]).sort(),
        ["E1", "E2"],
    );
    const masked = (hidden: string[]) => {
        const out = new Uint32Array(edges.length);
        maskHiddenEdges(edges, owned, (id) => hidden.includes(id), out);
        // An edge is drawn when its two ends differ.
        return Array.from({length: edges.length / 2}, (_, e) => out[e * 2] !== out[e * 2 + 1]);
    };
    assert.deepEqual(masked([]), [true, true, true, true, true, true, true, true]);
    // E1 hidden: its own three edges go, the shared edge stays (E2 still borders it), the beam stays.
    assert.deepEqual(masked(["E1"]), [false, true, false, false, true, true, true, true]);
    // Both hidden: only the unowned edge is left.
    assert.deepEqual(masked(["E1", "E2"]), [false, false, false, false, false, false, false, true]);
});
