// Hidden objects are never selected: the box selection's window and crossing skip what is hidden
// (directly, as a hidden branch, outside an isolation, or with its whole model switched off) and
// what a section plane cuts away whole, and the selection store drops hidden ranges whichever path
// hands them in. The identity matrix stands in for the camera, so world x and y are screen NDC.
import {beforeEach, test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";

import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {rangesInScreenRect} from "@/utils/bboxSelection/screenBox";
import {rangesInBox} from "@/utils/bboxSelection/rangeBounds";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {boxCutAway, rangeHidden, selectableInBounds, withoutHidden} from "@/utils/mesh_select/rangeVisibility";

const unit = {x0: 0, y0: 0, x1: 1, y1: 1};
const identity = new THREE.Matrix4();

/** Ten small triangles in a row inside the unit square: e0 at x = 0.05 ... e9 at x = 0.95. */
function row(): CustomBatchedMesh {
    const pos: number[] = [];
    const ranges = new Map<string, [number, number]>();
    for (let i = 0; i < 10; i++) {
        const x = 0.02 + i * 0.09;
        pos.push(x, 0.4, 0, x + 0.05, 0.4, 0, x, 0.5, 0);
        ranges.set(`e${i}`, [i * 3, 3]);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
    return new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), ranges, "row", true, null);
}

const all = Array.from({length: 10}, (_, i) => `e${i}`);
const sorted = (xs: string[]) => [...xs].sort();

for (const mode of ["window", "crossing"] as const) {
    test(`${mode}: hidden elements are not taken`, () => {
        const m = row();
        m.hideBatchDrawRange(["e1", "e2"]);
        const got = rangesInScreenRect(m, identity, unit, mode, m.getHiddenRanges());
        assert.deepEqual(sorted(got), sorted(all.filter((id) => id !== "e1" && id !== "e2")));
    });

    test(`${mode}: a hidden branch (its ranges hidden together) is not taken`, () => {
        const m = row();
        const branch = ["e3", "e4", "e5", "e6"];
        m.hideBatchDrawRange(branch);
        const got = rangesInScreenRect(m, identity, unit, mode, m.getHiddenRanges());
        assert.ok(!got.some((id) => branch.includes(id)));
        assert.equal(got.length, 6);
    });

    test(`${mode}: under an isolation only the isolated elements are taken`, () => {
        const m = row();
        m.isolateDrawRanges(new Set(["e0", "e9"]));
        assert.deepEqual(sorted(rangesInScreenRect(m, identity, unit, mode, m.getHiddenRanges())), ["e0", "e9"]);
    });

    test(`${mode}: what a section plane cuts away whole is not taken`, () => {
        const m = row();
        // Kept where x >= 0.5: e0..e4 sit wholly left of it, e5 straddles it (partly drawn, so
        // still taken), e6..e9 are drawn.
        const plane = new THREE.Plane(new THREE.Vector3(1, 0, 0), -0.5);
        const got = rangesInScreenRect(m, identity, unit, mode, m.getHiddenRanges(), [plane]);
        assert.deepEqual(sorted(got), ["e5", "e6", "e7", "e8", "e9"]);
    });
}

test("the 3D selection box skips hidden and cut-away elements too", () => {
    const m = row();
    m.hideBatchDrawRange(["e9"]);
    const box = {min: [0, 0, -1] as [number, number, number], max: [1, 1, 1] as [number, number, number]};
    const plane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0.2); // kept where x <= 0.2
    assert.deepEqual(sorted(rangesInBox(m, box, "partial", m.getHiddenRanges(), [plane])), ["e0", "e1"]);
});

test("selectableInBounds: the candidates neither hidden nor cut away", () => {
    const ids = ["a", "b", "c"];
    const bounds = [0, 0, 0, 1, 1, 1, 2, 0, 0, 3, 1, 1, 4, 0, 0, 5, 1, 1];
    const plane = {normal: {x: 1, y: 0, z: 0}, constant: -1.5}; // kept where x >= 1.5
    assert.deepEqual(selectableInBounds(ids, bounds, new Set(["c"]), [plane]), ["b"]);
    assert.deepEqual(selectableInBounds(ids, bounds, new Set(), null), ids);
});

test("boxCutAway: a box straddling the plane is kept", () => {
    const plane = {normal: {x: 0, y: 0, z: 1}, constant: 0}; // kept where z >= 0
    assert.ok(boxCutAway([0, 0, -2, 1, 1, -1], 0, [plane]));
    assert.ok(!boxCutAway([0, 0, -1, 1, 1, 1], 0, [plane]));
    assert.ok(!boxCutAway([0, 0, -2, 1, 1, -1], 0, []));
});

test("rangeHidden: a model switched off whole hides its ranges; a wrapper hides what its meshes hide", () => {
    const m = row();
    const model = new THREE.Group();
    model.add(m);
    assert.ok(!rangeHidden(m, "e0"));
    m.hideBatchDrawRange(["e0"]);
    assert.ok(rangeHidden(m, "e0"));
    assert.ok(rangeHidden(model, "e0"), "the wrapper's only owner hides it");
    assert.ok(!rangeHidden(model, "e1"));
    model.visible = false;
    assert.ok(rangeHidden(m, "e1"), "a hidden ancestor hides everything below it");
});

test("withoutHidden keeps the batch as is when nothing in it is hidden", () => {
    const m = row();
    const batch: [THREE.Object3D, string][] = [[m, "e0"], [m, "e1"]];
    assert.equal(withoutHidden(batch), batch);
    m.hideBatchDrawRange(["e1"]);
    assert.deepEqual(withoutHidden(batch).map(([, id]) => id), ["e0"]);
});

beforeEach(() => useSelectedObjectStore.getState().clearSelectedObjects());

test("the selection store never takes a hidden range, whichever path hands it in", () => {
    const m = row();
    m.hideBatchDrawRange(["e3", "e4"]);
    const sel = useSelectedObjectStore.getState();
    // A tree row over a partly hidden branch selects every range beneath it.
    sel.addBatchofMeshes(all.map((id) => [m, id] as [THREE.Object3D, string]));
    assert.deepEqual(sorted([...useSelectedObjectStore.getState().selectedObjects.get(m)!]), sorted(all.filter((id) => id !== "e3" && id !== "e4")));
    sel.clearSelectedObjects();
    useSelectedObjectStore.getState().addSelectedObject(m, "e3");
    assert.equal(useSelectedObjectStore.getState().selectedObjects.size, 0);
});
