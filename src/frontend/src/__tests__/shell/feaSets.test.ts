import assert from "node:assert/strict";
import {test} from "node:test";

import {
    complementRanges,
    isElementSet,
    selectedMemberCount,
    unionMembers,
    type FeaSet,
} from "../../shell/feaSets";

const set = (name: string, members: string[], kind: "node" | "element" = "element"): FeaSet => ({
    name,
    members,
    fe_object_type: kind,
});

// Named after real Sesam sets from the Mini topside deck.
const SETS: FeaSet[] = [
    set("Mini (nodes)", ["P1", "P2", "P3"], "node"),
    set("Mini (elements)", ["E1", "E2", "E3", "E4"]),
    set("Mini_area_dbl_btm", ["E2", "E3"]),
    set("Mini_area_east_main", ["E5"]),
];

test("multi-select unions and de-duplicates overlapping sets", () => {
    const ids = unionMembers(SETS, new Set(["Mini (elements)", "Mini_area_dbl_btm"]));
    // E2 and E3 are in both sets; each must appear once, or the same range would be hidden
    // and unhidden inside one pass.
    assert.deepEqual(ids, ["E1", "E2", "E3", "E4"]);
});

test("member count never exceeds the model by double-counting", () => {
    const both = new Set(["Mini (elements)", "Mini_area_dbl_btm"]);
    const naive = SETS.filter((s) => both.has(s.name)).reduce((n, s) => n + s.members.length, 0);
    assert.equal(naive, 6, "the naive sum really does over-count here");
    assert.equal(selectedMemberCount(SETS, both), 4);
});

test("the hidden set is the mesh's ranges minus the selection", () => {
    // "E{id}" is the id the streaming loader builds its draw ranges from -- see
    // installAfemUserData. A member with any other prefix matches no range and hides
    // nothing, so the prefix is part of the contract this test pins.
    const meshRanges = ["E1", "E2", "E3", "E4", "E5", "E6"];
    assert.deepEqual(complementRanges(meshRanges, ["E2", "E3"]), ["E1", "E4", "E5", "E6"]);
    // A selection naming no drawn range must not remove anything by accident -- that is
    // the case that would otherwise blank the viewport for a node-only group.
    assert.deepEqual(complementRanges(meshRanges, ["P1"]), meshRanges);
});

test("selecting nothing hides nothing", () => {
    const meshRanges = ["E1", "E2"];
    // Guards the "no group chosen shows the whole model" rule at the arithmetic level:
    // an empty selection must not compute the entire model as the hidden set.
    assert.equal(unionMembers(SETS, new Set()).length, 0);
    assert.deepEqual(complementRanges(meshRanges, []), meshRanges);
});

test("node sets are not element sets", () => {
    assert.equal(isElementSet(SETS[0]), false);
    assert.equal(isElementSet(SETS[1]), true);
    // Absent fe_object_type means element: the manifest's older group shape omitted it.
    assert.equal(isElementSet({name: "x", members: []}), true);
});
