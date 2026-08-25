import assert from "node:assert/strict";
import {test} from "node:test";

import {buildTreeIndices, describeNode} from "../../utils/tree_view/treeGraph";

// Same shape as the real hierarchy: a synthetic root whose children are the
// per-model file roots, then grouping levels, then geometry-bearing leaves.
// `node_name` is the discriminator the model loader sets — it is the merged-mesh
// name for a node with a draw range, and null for a pure grouping level.
//   root
//   └─ fileA
//      ├─ g1 ─ (l1*, l2*)      * = has geometry
//      ├─ g2 ─ (l3*)
//      └─ g3                    (a container with no children at all)
const leaf = (id: string) => ({id, name: id, children: [], node_name: `mesh_${id}`} as any);
const l1 = leaf("l1"), l2 = leaf("l2"), l3 = leaf("l3");
const g1 = {id: "g1", name: "g1", children: [l1, l2], node_name: null} as any;
const g2 = {id: "g2", name: "g2", children: [l3], node_name: null} as any;
const g3 = {id: "g3", name: "g3", children: [], node_name: null} as any;
const fileA = {id: "fileA", name: "fileA", children: [g1, g2, g3], node_name: null} as any;
const root = {id: "root", name: "", children: [fileA]} as any;

const idx = buildTreeIndices(root);

test("a geometry-bearing leaf reports itself as such", () => {
    const f = describeNode(l1, idx);
    assert.equal(f.hasGeometry, true);
    assert.equal(f.childCount, 0);
    assert.equal(f.descendantGeometryCount, 1); // itself
});

test("a container reports no geometry of its own but counts what it holds", () => {
    const f = describeNode(g1, idx);
    assert.equal(f.hasGeometry, false);
    assert.equal(f.childCount, 2);
    assert.equal(f.descendantGeometryCount, 2);
});

test("counts aggregate over the whole subtree, not just direct children", () => {
    const f = describeNode(fileA, idx);
    assert.equal(f.childCount, 3);
    assert.equal(f.descendantGeometryCount, 3); // l1, l2, l3
});

test("an empty container still describes itself instead of vanishing", () => {
    // The case that produced a blank panel: nothing selected in 3D, no
    // metadata row, no name from any descendant. It is a real place in the
    // model and must say so.
    const f = describeNode(g3, idx);
    assert.equal(f.hasGeometry, false);
    assert.equal(f.childCount, 0);
    assert.equal(f.descendantGeometryCount, 0);
    assert.deepEqual(f.ancestry.map((a) => a.id), ["fileA"]);
});

test("ancestry is nearest-first and excludes the node and the synthetic root", () => {
    const f = describeNode(l1, idx);
    assert.deepEqual(f.ancestry.map((a) => a.id), ["g1", "fileA"]);
    assert.ok(!f.ancestry.includes(root));
    assert.ok(!f.ancestry.includes(l1));
});

test("a file root has no ancestry to show", () => {
    assert.deepEqual(describeNode(fileA, idx).ancestry, []);
});

test("identity is the node id, which the display name cannot supply", () => {
    // Two distinct nodes sharing a display name is normal in a real model;
    // that is the whole reason the panel needs an id rather than a name.
    const dupA = {id: "dupA", name: "BEAM1", children: [], node_name: "mesh_a"} as any;
    const dupB = {id: "dupB", name: "BEAM1", children: [], node_name: "mesh_b"} as any;
    const holder = {id: "holder", name: "holder", children: [dupA, dupB], node_name: null} as any;
    const local = buildTreeIndices({id: "r", name: "", children: [holder]} as any);
    assert.equal(describeNode(dupA, local).name, describeNode(dupB, local).name);
    assert.notEqual(describeNode(dupA, local).id, describeNode(dupB, local).id);
});

test("a cycle in the child links cannot hang the subtree walk", () => {
    // `children` is plain data with no integrity enforcement, so a malformed
    // tree must degrade rather than lock the UI thread.
    //
    // The indices are hand-built rather than derived: `buildTreeIndices` walks
    // `children` with no visited set of its own, so feeding it a cycle would
    // hang before `describeNode` was ever reached. That is a latent sharp edge
    // in the existing helper — out of scope here, and unreachable from the
    // model loader, which builds the tree from a parent map — but it does mean
    // this test has to construct the input directly to test what it claims to.
    const a = {id: "a", name: "a", children: [] as any[], node_name: null} as any;
    const b = {id: "b", name: "b", children: [a], node_name: null} as any;
    a.children.push(b); // a -> b -> a
    const root = {id: "r", name: "", children: [b]} as any;
    const local = {
        parent: new Map<string, any>([["b", root], ["a", b]]),
        byId: new Map<string, any>([["r", root], ["b", b], ["a", a]]),
        root,
    };
    const f = describeNode(b, local);
    assert.equal(f.descendantGeometryCount, 0);
    assert.equal(f.childCount, 1);
    assert.deepEqual(f.ancestry, []); // b's only ancestor is the synthetic root
});
