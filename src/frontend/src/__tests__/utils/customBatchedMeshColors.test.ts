// Colours the user gives objects on the three.js viewport: a colour per draw range through the
// mesh's one colour attribute, taken back exactly, kept through hiding and a feature's own
// colouring, and cheap for thousands of objects.
import assert from "node:assert/strict";
import {test} from "node:test";
import * as THREE from "three";

import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

/** A mesh of `n` triangles, each its own draw range `r<i>`, in a mid-grey standard material. */
function mesh(n: number): CustomBatchedMesh {
    const pos = new Float32Array(n * 9);
    for (let i = 0; i < n; i++) pos.set([i, 0, 0, i + 1, 0, 0, i + 1, 1, 0], i * 9);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setIndex(new THREE.BufferAttribute(Uint32Array.from({length: n * 3}, (_, i) => i), 1));
    const ranges = new Map<string, [number, number]>();
    for (let i = 0; i < n; i++) ranges.set(`r${i}`, [i * 3, 3]);
    const material = new THREE.MeshStandardMaterial({color: new THREE.Color(0.5, 0.25, 0.125), metalness: 0.1, roughness: 0.7});
    return new CustomBatchedMesh(g, material, ranges, "m", true, null);
}

const colorOf = (m: CustomBatchedMesh, range: number): number[] => {
    const a = m.geometry.getAttribute("color") as THREE.BufferAttribute;
    return [a.getX(range * 3), a.getY(range * 3), a.getZ(range * 3)].map((c) => Math.round(c * 1000) / 1000);
};
const base = (m: CustomBatchedMesh) => (m.originalMaterial as THREE.MeshStandardMaterial);

test("a recoloured range is exactly the colour asked for; the others keep the mesh's own", () => {
    const m = mesh(4);
    assert.equal(m.setUserColors(["r1", "r3"], 0xff0000), 2);
    assert.equal(m.userColorCount, 2);
    // The material is white while colours are per vertex, so a colour is not multiplied by another.
    assert.deepEqual(base(m).color.toArray(), [1, 1, 1]);
    assert.equal(base(m).vertexColors, true);
    assert.deepEqual(colorOf(m, 1), [1, 0, 0]);
    assert.deepEqual(colorOf(m, 0), [0.5, 0.25, 0.125]);
    assert.equal(m.userColorOf("r1"), 0xffff0000);
    assert.equal(m.userColorOf("r0"), 0);
    // sRGB in, linear light in the attribute: #808080 is 0.216.
    m.setUserColors(["r0"], 0x808080);
    assert.deepEqual(colorOf(m, 0), [0.216, 0.216, 0.216]);
    // One material and one group: no draw call per object.
    assert.equal(m.geometry.groups.length, 1);
    assert.equal((m.material as THREE.Material[]).length, 3);
});

test("taking the colours back restores the material exactly and drops the attribute", () => {
    const m = mesh(3);
    const metal = base(m).metalness;
    m.setUserColors(null, 0x00ff00);
    assert.equal(m.userColorCount, 3);
    assert.equal(m.setUserColors(["r0"], null), 1);
    assert.deepEqual(colorOf(m, 0), [0.5, 0.25, 0.125]);
    assert.equal(m.setUserColors(null, null), 2);
    assert.equal(m.userColorCount, 0);
    assert.equal(m.geometry.getAttribute("color"), undefined);
    assert.deepEqual(base(m).color.toArray(), [0.5, 0.25, 0.125]);
    assert.equal(base(m).vertexColors, false);
    assert.equal(base(m).metalness, metal, "lit by the mesh's own material throughout");
    assert.equal(m.setUserColors(null, null), 0, "nothing to take back");
    assert.equal(m.setUserColors(["nope"], 0x123456), 0, "an unknown id changes nothing");
    assert.equal(m.geometry.getAttribute("color"), undefined, "and leaves the mesh as it was");
});

test("hiding, showing and isolating keep the colours; the selection is drawn over them", () => {
    const m = mesh(4);
    m.setUserColors(["r1"], 0x0000ff);
    m.hideBatchDrawRange(["r1"]);
    m.unhideBatchDrawRange(["r1"]);
    m.isolateDrawRanges(new Set(["r1"]));
    m.unhideAllDrawRanges();
    assert.deepEqual(colorOf(m, 1), [0, 0, 1]);
    // A mesh coloured by vertex shows its selection as an overlay, so the highlight is not lost in
    // the colour and the colour is still there when the selection goes.
    m.updateSelectionGroups(["r1"]);
    const overlay = m.children.find((c) => m.isHighlightOverlay(c)) as THREE.Mesh | undefined;
    assert.ok(overlay?.visible, "the selection overlay is drawn");
    m.updateSelectionGroups([]);
    assert.deepEqual(colorOf(m, 1), [0, 0, 1]);
});

test("a snapshot puts the colours back, and none takes them all away", () => {
    const m = mesh(3);
    assert.equal(m.userColorSnapshot(), null);
    m.setUserColors(["r0"], 0xff0000);
    const one = m.userColorSnapshot();
    m.setUserColors(["r1", "r2"], 0x00ff00);
    const three = m.userColorSnapshot();
    m.restoreUserColors(one);
    assert.equal(m.userColorCount, 1);
    assert.deepEqual(colorOf(m, 1), [0.5, 0.25, 0.125]);
    m.restoreUserColors(null);
    assert.equal(m.userColorCount, 0);
    assert.equal(m.geometry.getAttribute("color"), undefined);
    m.restoreUserColors(three);
    assert.deepEqual([colorOf(m, 0), colorOf(m, 1)], [[1, 0, 0], [0, 1, 0]]);
    m.restoreUserColors(new Uint32Array(7));
    assert.equal(m.userColorCount, 3, "a snapshot of another mesh is ignored");
});

test("a mesh whose vertex colours are someone else's is left alone", () => {
    const m = mesh(2);
    // A painted FEA result, a file's vertex colours, a colouring by property.
    m.setRangeColors(new Map([["r0", new THREE.Color(1, 1, 0)]]));
    assert.equal(m.acceptsUserColors(), false);
    assert.equal(m.setUserColors(null, 0xff0000), 0);
    assert.deepEqual(colorOf(m, 0), [1, 1, 0]);
});

test("a feature's colouring over recoloured objects goes without taking the user's colours", () => {
    const m = mesh(3);
    m.setUserColors(["r0"], 0xff0000);
    m.setRangeColors(new Map([["r1", new THREE.Color(0, 1, 1)]]));
    assert.deepEqual(colorOf(m, 1), [0, 1, 1]);
    m.disableVertexColorsAndResetMaterial();
    assert.deepEqual(colorOf(m, 0), [1, 0, 0]);
    assert.deepEqual(colorOf(m, 1), [0.5, 0.25, 0.125]);
    assert.equal(m.userColorCount, 1);
});

test("the mesh's own colour can change under the user's colours (the default colour setting)", () => {
    const m = mesh(2);
    m.setOwnColor(0.1, 0.2, 0.3);
    assert.deepEqual(base(m).color.toArray().map((c) => Math.round(c * 10) / 10), [0.1, 0.2, 0.3]);
    m.setUserColors(["r0"], 0xffffff);
    m.setOwnColor(0.4, 0.4, 0.4);
    assert.deepEqual(colorOf(m, 1), [0.4, 0.4, 0.4]);
    assert.deepEqual(colorOf(m, 0), [1, 1, 1]);
    m.setUserColors(null, null);
    assert.deepEqual(base(m).color.toArray().map((c) => Math.round(c * 10) / 10), [0.4, 0.4, 0.4]);
});

test("recolouring 10,000 objects is one pass: milliseconds, one group, no material per object", () => {
    const m = mesh(10_000);
    const ids = Array.from({length: 10_000}, (_, i) => `r${i}`);
    m.setUserColors(["r0"], 0x111111); // the attribute and the id index are made once
    const t0 = performance.now();
    assert.equal(m.setUserColors(ids, 0x3366cc), 10_000);
    const recolour = performance.now() - t0;
    const t1 = performance.now();
    assert.equal(m.setUserColors(null, null), 10_000);
    const reset = performance.now() - t1;
    console.log(`three.js: recolour 10k objects ${recolour.toFixed(2)} ms, reset ${reset.toFixed(2)} ms`);
    assert.ok(recolour < 100 && reset < 100, `recolour ${recolour.toFixed(1)} ms, reset ${reset.toFixed(1)} ms`);
    assert.equal(m.geometry.groups.length, 1);
});
