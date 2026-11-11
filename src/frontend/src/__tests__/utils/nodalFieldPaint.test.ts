// One step of a nodal field as plain numbers: the colour of each node from the colour map and the
// range, the displacement from the translation components. No renderer is involved, and the
// three.js viewport's painter writes exactly these numbers into its geometry.
import assert from "node:assert/strict";
import {test} from "node:test";

import * as THREE from "three";

import type {FeaManifestField} from "@/services/viewerApi";
import {applyFieldToMesh} from "@/utils/scene/fea/applyField";
import {getColormap, linearColormap} from "@/utils/scene/fea/colormaps";
import {fieldScalarRange, paintNodalField} from "@/utils/scene/fea/nodalFieldPaint";

function field(over: Partial<FeaManifestField> = {}): FeaManifestField {
    return {
        name_canonical: "displacement",
        name_native: "U",
        kind: "vector3",
        category: "displacement",
        support: "nodal",
        analysis_kind: "static",
        components: ["X", "Y", "Z"],
        n_steps: 1,
        steps: [{i: 0, value: 1, label: "1"}],
        scalar_range: {magnitude: [0, 5], X: [-3, 3]},
        default_view: {reduction: "magnitude", colormap: "viridis"},
        ...over,
    } as FeaManifestField;
}

/** Three nodes displaced by (0,0,0), (3,4,0) and (-3,0,0). */
const U = new Float32Array([0, 0, 0, 3, 4, 0, -3, 0, 0]);

// What a kernel hands a viewport is linear light: the colour map's sRGB decoded (colormaps.ts).
function colorAt(name: string, t: number): number[] {
    const out = new Float32Array(3);
    linearColormap(getColormap(name))(t, out, 0);
    return [...out];
}

function paint(args: Partial<Parameters<typeof paintNodalField>[0]> = {}) {
    const displacement = new Float32Array(9).fill(7);
    const colors = new Float32Array(9).fill(7);
    paintNodalField({nPoints: 3, colorField: field(), colorStepValues: U, reduction: "magnitude", colormap: "viridis", ...args}, displacement, colors);
    return {displacement, colors: [0, 1, 2].map((v) => [...colors.subarray(v * 3, v * 3 + 3)])};
}

test("the magnitude is mapped over the field's range: lowest node at the bottom of the map, highest at the top", () => {
    const {colors} = paint();
    assert.deepEqual(colors[0], colorAt("viridis", 0));
    assert.deepEqual(colors[1], colorAt("viridis", 1)); // |(3,4,0)| = 5 of [0, 5]
    colors[2].forEach((c, i) => assert.ok(Math.abs(c - colorAt("viridis", 3 / 5)[i]) < 1e-6));
});

test("a component is mapped over its own signed range", () => {
    const {colors} = paint({reduction: "X"});
    assert.deepEqual(colors[0], colorAt("viridis", 0.5));
    assert.deepEqual(colors[1], colorAt("viridis", 1));
    assert.deepEqual(colors[2], colorAt("viridis", 0));
});

test("a measured range takes the place of the field's, and a pinned end takes the place of either", () => {
    const measured = paint({autoRange: [0, 10]});
    assert.deepEqual(measured.colors[1], colorAt("viridis", 0.5));
    const pinned = paint({autoRange: [0, 10], contour: {levels: null, min: null, max: 20}});
    assert.deepEqual(pinned.colors[1], colorAt("viridis", 0.25));
});

test("bands draw every value of a band in one colour", () => {
    const values = new Float32Array([0.5, 0, 0, 0.9, 0, 0, 4.9, 0, 0]);
    const {colors} = paint({colorStepValues: values, contour: {levels: 5, min: null, max: null}});
    assert.deepEqual(colors[0], colors[1], "0.5 and 0.9 are both in the first of five bands over [0, 5]");
    assert.notDeepEqual(colors[0], colors[2]);
});

test("another colour map gives other colours for the same values", () => {
    assert.deepEqual(paint({colormap: "jet"}).colors[1], colorAt("jet", 1));
    assert.notDeepEqual(paint({colormap: "jet"}).colors[1], paint().colors[1]);
});

test("a one-component field's 'scalar' reduction is the value itself", () => {
    const temperature = field({components: ["T"], scalar_range: {scalar: [10, 20]}, category: "other"} as Partial<FeaManifestField>);
    const {colors} = paint({colorField: temperature, colorStepValues: new Float32Array([10, 15, 20]), reduction: "scalar"});
    assert.deepEqual(colors, [colorAt("viridis", 0), colorAt("viridis", 0.5), colorAt("viridis", 1)]);
});

test("a value that is not a number is drawn at the bottom of the scale, not as garbage", () => {
    const {colors} = paint({colorStepValues: new Float32Array([NaN, 0, 0, 3, 4, 0, -3, 0, 0])});
    assert.deepEqual(colors[0], colorAt("viridis", 0));
});

test("the displacement is the warp field's translation, not scaled", () => {
    const {displacement} = paint({warpField: field(), warpStepValues: U});
    assert.deepEqual([...displacement], [...U]);
});

test("a warp field with rotations is read by its translation components", () => {
    const six = field({components: ["RX", "RY", "RZ", "UX", "UY", "UZ"]} as Partial<FeaManifestField>);
    const values = new Float32Array([9, 9, 9, 1, 2, 3, 9, 9, 9, 4, 5, 6, 9, 9, 9, 7, 8, 9]);
    const {displacement} = paint({warpField: six, warpStepValues: values});
    assert.deepEqual([...displacement], [1, 2, 3, 4, 5, 6, 7, 8, 9]);
});

test("without a warp field the displacement is zero, whatever the output held", () => {
    assert.deepEqual([...paint().displacement], new Array(9).fill(0));
});

test("values that do not fit the mesh are refused", () => {
    assert.throws(() => paint({colorStepValues: new Float32Array(6)}), /colorStepValues length 6/);
    assert.throws(() => paint({warpField: field(), warpStepValues: new Float32Array(6)}), /warpStepValues length 6/);
});

test("the field's range of a reduction falls back to its first component's, then to 0..1", () => {
    assert.deepEqual(fieldScalarRange(field(), "magnitude"), [0, 5]);
    assert.deepEqual(fieldScalarRange(field(), "Y"), [-3, 3]);
    assert.deepEqual(fieldScalarRange(field({scalar_range: {}} as Partial<FeaManifestField>), "magnitude"), [0, 1]);
});

test("the three.js painter draws exactly the kernel's numbers", () => {
    const base = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(base.slice(), 3));
    geometry.setIndex([0, 1, 2]);
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
    applyFieldToMesh({
        mesh, basePositions: base, colorField: field(), colorStepValues: U, reduction: "magnitude",
        warpField: field(), warpStepValues: U, displacementScale: 2.5, colormap: "viridis",
    });
    const expected = paint({warpField: field(), warpStepValues: U});
    assert.deepEqual([...(geometry.getAttribute("color").array as Float32Array)], expected.colors.flat());
    assert.deepEqual([...(geometry.morphAttributes.position[0].array as Float32Array)], [...expected.displacement]);
    assert.deepEqual([...(geometry.getAttribute("position").array as Float32Array)], [...base], "the base stays undeformed");
    assert.equal(mesh.morphTargetInfluences?.[0], 2.5);
    assert.equal(geometry.morphTargetsRelative, true);
    assert.equal((mesh.material as THREE.MeshStandardMaterial).vertexColors, true);
});
