import assert from "node:assert/strict";
import {test} from "node:test";

import {
    complementRanges,
    filterSets,
    isElementSet,
    rangeBetween,
    selectedMemberCount,
    unionMembers,
    type FeaSet,
} from "../../shell/feaSets";

const set = (name: string, members: string[], kind: "node" | "element" = "element"): FeaSet => ({
    name,
    members,
    fe_object_type: kind,
});

// Named after real Sesam sets from the Mini topside deck, because the compound-name shape
// is exactly what the search has to cope with.
const SETS: FeaSet[] = [
    set("Mini (nodes)", ["P1", "P2", "P3"], "node"),
    set("Mini (elements)", ["E1", "E2", "E3", "E4"]),
    set("Mini_area_dbl_btm", ["E2", "E3"]),
    set("Mini_area_east_main", ["E5"]),
];

test("search matches mid-name, not just the prefix", () => {
    // The whole point: nobody recalls a compound set name left-to-right.
    assert.deepEqual(
        filterSets(SETS, "btm").map((s) => s.name),
        ["Mini_area_dbl_btm"],
    );
    assert.deepEqual(
        filterSets(SETS, "AREA").map((s) => s.name),
        ["Mini_area_dbl_btm", "Mini_area_east_main"],
    );
    assert.equal(filterSets(SETS, "   ").length, SETS.length, "a blank query filters nothing");
});

test("multi-select unions and de-duplicates overlapping sets", () => {
    const ids = unionMembers(SETS, new Set(["Mini (elements)", "Mini_area_dbl_btm"]));
    // E2 and E3 are in both sets; each must appear once or the selection store gets a
    // doubled entry.
    assert.deepEqual(ids, ["E1", "E2", "E3", "E4"]);
    assert.equal(selectedMemberCount(SETS, new Set(["Mini (elements)", "Mini_area_dbl_btm"])), 4);
});

test("member count never exceeds the model by double-counting", () => {
    const both = new Set(["Mini (elements)", "Mini_area_dbl_btm"]);
    const naive = SETS.filter((s) => both.has(s.name)).reduce((n, s) => n + s.members.length, 0);
    assert.equal(naive, 6, "the naive sum really does over-count here");
    assert.equal(selectedMemberCount(SETS, both), 4);
});

test("the ghost set is the mesh's ranges minus the selection", () => {
    // "E{id}" is the id the streaming loader builds its draw ranges from -- see
    // installAfemUserData. A member with any other prefix matches no range and selects
    // nothing, so the prefix is part of the contract this test pins.
    const meshRanges = ["E1", "E2", "E3", "E4", "E5", "E6"];
    assert.deepEqual(complementRanges(meshRanges, ["E2", "E3"]), ["E1", "E4", "E5", "E6"]);
    // Selecting a range the mesh does not draw must not remove anything by accident.
    assert.deepEqual(complementRanges(meshRanges, ["P1"]), meshRanges);
});

test("node sets are not element sets", () => {
    assert.equal(isElementSet(SETS[0]), false);
    assert.equal(isElementSet(SETS[1]), true);
    // Absent fe_object_type means element: the manifest's older group shape omitted it.
    assert.equal(isElementSet({name: "x", members: []}), true);
});

test("shift-range spans the visible rows in either direction", () => {
    const visible = filterSets(SETS, "");
    assert.deepEqual(rangeBetween(visible, "Mini (elements)", "Mini_area_east_main"), [
        "Mini (elements)",
        "Mini_area_dbl_btm",
        "Mini_area_east_main",
    ]);
    // Dragging upwards gives the same span.
    assert.deepEqual(rangeBetween(visible, "Mini_area_east_main", "Mini (elements)"), [
        "Mini (elements)",
        "Mini_area_dbl_btm",
        "Mini_area_east_main",
    ]);
});

test("shift-range under a filter takes only what is on screen", () => {
    // Anchored on the FILTERED list: a range must never quietly include rows the search
    // is hiding.
    const visible = filterSets(SETS, "area");
    assert.deepEqual(rangeBetween(visible, "Mini_area_dbl_btm", "Mini_area_east_main"), [
        "Mini_area_dbl_btm",
        "Mini_area_east_main",
    ]);
    // An anchor that scrolled out of the filter degrades to a plain click.
    assert.deepEqual(rangeBetween(visible, "Mini (nodes)", "Mini_area_east_main"), ["Mini_area_east_main"]);
});
