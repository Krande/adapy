import assert from "node:assert/strict";
import {describe, test} from "node:test";

import {
    complementRanges,
    drawRangeIdFor,
    isElementSet,
    unionMembers,
    type FeaSet,
} from "@/utils/scene/fea/feaSets";

const set = (name: string, members: string[], kind: "node" | "element" = "element"): FeaSet => ({
    name,
    members,
    fe_object_type: kind,
});

const SETS: FeaSet[] = [
    set("Deck (nodes)", ["P1", "P2", "P3"], "node"),
    set("Deck (elements)", ["E1", "E2", "E3", "E4"]),
    set("Double bottom", ["E2", "E3"]),
    set("East main", ["E5"]),
];

test("multi-select unions and de-duplicates overlapping sets", () => {
    const ids = unionMembers(SETS, new Set(["Deck (elements)", "Double bottom"]));
    // E2 and E3 are in both sets; each must appear once, or the same range would be hidden
    // and unhidden inside one pass.
    assert.deepEqual(ids, ["E1", "E2", "E3", "E4"]);
});

test("the hidden set is the mesh's ranges minus the selection", () => {
    // "E{id}" is the id the streaming loader builds its draw ranges from. A member with
    // any other prefix matches no range and hides nothing, so the prefix is part of the
    // contract this test pins.
    const meshRanges = ["E1", "E2", "E3", "E4", "E5", "E6"];
    assert.deepEqual(complementRanges(meshRanges, ["E2", "E3"]), ["E1", "E4", "E5", "E6"]);
    // A selection naming no drawn range must not remove anything by accident -- that is
    // the case that would otherwise blank the viewport for a node-only group.
    assert.deepEqual(complementRanges(meshRanges, ["P1"]), meshRanges);
});

test("selecting nothing hides nothing", () => {
    const meshRanges = ["E1", "E2"];
    assert.equal(unionMembers(SETS, new Set()).length, 0);
    assert.deepEqual(complementRanges(meshRanges, []), meshRanges);
});

test("node sets are not element sets", () => {
    assert.equal(isElementSet(SETS[0]), false);
    assert.equal(isElementSet(SETS[1]), true);
    // Absent fe_object_type means element: the manifest's older group shape omitted it.
    assert.equal(isElementSet({name: "x", members: []}), true);
});

describe("member ids as the mesh names its draw ranges", () => {
    test("EL becomes E", () => {
        assert.equal(drawRangeIdFor("EL1"), "E1");
        assert.equal(drawRangeIdFor("EL5751"), "E5751");
    });

    test("an id already in range form is left alone", () => {
        assert.equal(drawRangeIdFor("E42"), "E42");
    });

    test("a node member has no range and is dropped", () => {
        assert.equal(drawRangeIdFor("P1"), null);
    });

    test("an unrecognised shape passes through rather than vanishing", () => {
        assert.equal(drawRangeIdFor("BEAM-7"), "BEAM-7");
    });

    test("unionMembers yields ids the mesh can actually match", () => {
        const sets: FeaSet[] = [
            {name: "Deck", members: ["EL1", "EL2"], fe_object_type: "element"},
            {name: "Deck", members: ["P1", "P2"], fe_object_type: "node"},
        ];
        assert.deepEqual(unionMembers(sets, new Set(["Deck"])), ["E1", "E2"]);
    });

    test("still de-duplicates across overlapping sets", () => {
        const sets: FeaSet[] = [
            {name: "A", members: ["EL1", "EL2"], fe_object_type: "element"},
            {name: "B", members: ["EL2", "EL3"], fe_object_type: "element"},
        ];
        assert.deepEqual(unionMembers(sets, new Set(["A", "B"])), ["E1", "E2", "E3"]);
    });
});
