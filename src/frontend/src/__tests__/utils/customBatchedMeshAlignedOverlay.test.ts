// An overlay painted over a batched mesh's elements -- a plugin's colour field hung on the FEA
// mesh it got from `getActiveFeaMesh`, a de-indexed copy of it whose vertex k is index entry k --
// hides with them: a hidden element has no colour on top, whenever the overlay was made (before
// the hide, or after it when a View setting repainted), and shows again on unhide.
import assert from "node:assert/strict";
import {test} from "node:test";
import * as THREE from "three";

import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

/** Two triangles, ranges `E1` (index 0..3) and `E2` (index 3..6). */
function mesh(): CustomBatchedMesh {
    const pos = new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 2, 0, 0, 2, 1, 0]);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setIndex(new THREE.BufferAttribute(Uint32Array.from({length: 6}, (_, i) => i), 1));
    const ranges = new Map<string, [number, number]>([["E1", [0, 3]], ["E2", [3, 3]]]);
    return new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), ranges, "m", false, null);
}

/** A colour overlay as a plugin builds it: the mesh's triangles de-indexed, vertex-coloured. */
function deindexedOverlay(m: THREE.Mesh): THREE.Mesh {
    const n = m.geometry.index!.count;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(n * 4).fill(1), 4));
    return new THREE.Mesh(g, new THREE.MeshBasicMaterial({vertexColors: true}));
}

/** The parts of `overlay` drawn: [start, count] of each group whose material is visible. */
function drawn(overlay: THREE.Mesh): number[][] {
    const mats = Array.isArray(overlay.material) ? overlay.material : [overlay.material];
    const groups = overlay.geometry.groups.length ? overlay.geometry.groups : [{start: 0, count: 6, materialIndex: 0}];
    return groups.filter((g) => mats[g.materialIndex ?? 0].visible).map((g) => [g.start, g.count]);
}

test("the colours a hidden element gets in an overlay over it: none", () => {
    const m = mesh();
    const overlay = deindexedOverlay(m);
    const own = overlay.material;
    m.add(overlay);
    assert.deepEqual(drawn(overlay), [[0, 6]], "nothing hidden: all of it");
    m.hideBatchDrawRange(["E1"]);
    assert.deepEqual(drawn(overlay), [[3, 3]], "E1 hidden: only E2's colour is drawn");
    m.unhideBatchDrawRange(["E1"]);
    assert.deepEqual(drawn(overlay), [[0, 6]]);
    assert.equal(overlay.material, own, "its own material back, untouched");
});

test("an overlay made after the hide (a View setting repainted) starts with the hidden elements left out", () => {
    const m = mesh();
    m.hideBatchDrawRange(["E2"]);
    const overlay = deindexedOverlay(m);
    m.add(overlay);
    assert.deepEqual(drawn(overlay), [[0, 3]]);
    m.isolateDrawRanges(new Set(["E2"]));
    assert.deepEqual(drawn(overlay), [[3, 3]], "isolate: only what is kept");
    m.unhideAllDrawRanges();
    assert.deepEqual(drawn(overlay), [[0, 6]]);
});

test("a ghosted hidden appearance still leaves an overlay's colour out; other children are left alone", () => {
    const m = mesh();
    m.setHiddenAppearance("ghost", 0.2);
    const overlay = deindexedOverlay(m);
    const other = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
    const otherMat = other.material;
    const otherGroups = other.geometry.groups.length;
    m.add(overlay, other);
    m.hideBatchDrawRange(["E1"]);
    assert.deepEqual(drawn(overlay), [[3, 3]]);
    assert.equal(other.material, otherMat, "not range-aligned: not touched");
    assert.equal(other.geometry.groups.length, otherGroups);
    assert.equal(m.isAlignedOverlay(overlay), true);
    assert.equal(m.isAlignedOverlay(other), false);
});
