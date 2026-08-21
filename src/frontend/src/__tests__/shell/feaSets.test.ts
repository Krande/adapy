import assert from "node:assert/strict";
import {test} from "node:test";

import {
    GROUPS_ROOT_ID,
    buildGroupsRoot,
    complementRanges,
    groupNameFromId,
    groupNodeId,
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

test("group row ids round-trip and never collide with model nodes", () => {
    assert.equal(groupNameFromId(groupNodeId("Mini_area_dbl_btm")), "Mini_area_dbl_btm");
    // A group whose name happens to match a mesh name must still be distinguishable, or
    // selecting it would be routed through the mesh handler.
    assert.equal(groupNameFromId("node0"), null);
    assert.equal(groupNameFromId(GROUPS_ROOT_ID), null);
    // Names containing the separator survive, because slicing is by prefix length.
    assert.equal(groupNameFromId(groupNodeId("a:b:c")), "a:b:c");
});

test("the Groups root carries counts, and vanishes when there are no sets", () => {
    assert.equal(buildGroupsRoot([]), null, "an empty Groups row would be permanent clutter");
    const root = buildGroupsRoot(SETS);
    assert.ok(root);
    assert.equal(root.id, GROUPS_ROOT_ID);
    assert.equal(root.name, "Groups");
    assert.equal(root.meta, "4");
    assert.equal(root.children.length, 4);
    // Node groups say so; element groups are the unmarked default.
    assert.equal(root.children[0].meta, "3 n");
    assert.equal(root.children[1].meta, "4");
    // Children are leaves: react-arborist draws a toggle for anything with children, and a
    // group row that looks expandable but is not is a dead end.
    assert.ok(root.children.every((c) => c.children.length === 0));
});
