import assert from "node:assert/strict";
import {test} from "node:test";

import {buildTreeIndices, lowestCommonAncestor, selectableParent} from "../../utils/tree_view/treeGraph";

// A small tree mirroring the real shape: a synthetic root whose children are the
// per-model file roots; each file has groups, each group has leaf elements.
//   root
//   └─ fileA
//      ├─ g1 ─ (l1, l2)
//      └─ g2 ─ (l3)
const leaf = (id: string) => ({id, name: id, children: []} as any);
const l1 = leaf("l1"), l2 = leaf("l2"), l3 = leaf("l3");
const g1 = {id: "g1", name: "g1", children: [l1, l2]} as any;
const g2 = {id: "g2", name: "g2", children: [l3]} as any;
const fileA = {id: "fileA", name: "fileA", children: [g1, g2]} as any;
const root = {id: "root", name: "root", children: [fileA]} as any;

const idx = buildTreeIndices(root);

test("buildTreeIndices maps parents and all ids", () => {
    assert.equal(idx.parent.get("l1"), g1);
    assert.equal(idx.parent.get("g1"), fileA);
    assert.equal(idx.parent.get("fileA"), root);
    assert.equal(idx.parent.get("root"), undefined); // root has no parent
    assert.equal(idx.byId.size, 7);
    assert.equal(idx.byId.get("l3"), l3);
});

test("LCA of a single leaf is the leaf itself", () => {
    assert.equal(lowestCommonAncestor([l1], idx), l1);
});

test("LCA of siblings is their group; across groups is the file", () => {
    assert.equal(lowestCommonAncestor([l1, l2], idx), g1);
    assert.equal(lowestCommonAncestor([l1, l3], idx), fileA);
    assert.equal(lowestCommonAncestor([l1, l2, l3], idx), fileA);
});

test("LCA of an empty selection is null", () => {
    assert.equal(lowestCommonAncestor([], idx), null);
});

test("selectableParent climbs but stops at the file root (never the synthetic root)", () => {
    assert.equal(selectableParent(l1, idx), g1);
    assert.equal(selectableParent(g1, idx), fileA);
    assert.equal(selectableParent(fileA, idx), null); // parent is the synthetic root -> not selectable
});
