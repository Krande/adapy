// Core's element verbs for plugins: an element set's outline that stays on the warped surface,
// and element paint on the beam solids.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";
import * as THREE from "three";
import type {LineSegments2} from "three/examples/jsm/lines/LineSegments2";

import {useModelSessionStore, type FeaSessionHandle} from "@/state/modelSession";
import {addElementOutlines, boundaryEdges, paintFeaElements} from "@/plugins/sceneOverlays";

/** Two unit quads side by side in z=0 (elements 1 and 2), vertices split per element, as the FEA mesh
 *  stores them; one relative morph that lifts every vertex by 1 in z. */
function feaMesh(): THREE.Mesh & {drawRanges: Map<string, [number, number]>} {
    const quad = (x: number) => [x, 0, 0, x + 1, 0, 0, x + 1, 1, 0, x, 1, 0];
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.Float32BufferAttribute([...quad(0), ...quad(1)], 3));
    geom.setIndex([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]);
    geom.morphAttributes.position = [new THREE.Float32BufferAttribute(new Array(8).fill([0, 0, 1]).flat(), 3)];
    geom.morphTargetsRelative = true;
    const mesh = new THREE.Mesh(geom) as THREE.Mesh & {drawRanges: Map<string, [number, number]>};
    mesh.morphTargetInfluences = [0];
    mesh.drawRanges = new Map([["E1", [0, 6]], ["E2", [6, 6]]]);
    return mesh;
}

function open(mesh: THREE.Mesh, beamSolidMesh?: THREE.Mesh): void {
    const session = useModelSessionStore.getState().open({sourceName: "m.SIN", kind: "fea"});
    session.fea = {sourceName: "m.SIN", mesh, beamSolidMesh} as unknown as FeaSessionHandle;
}

afterEach(() => useModelSessionStore.getState().close());

test("an element set's outline drops the edge its elements share", () => {
    const mesh = feaMesh();
    const pos = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    // One quad: its 4 sides. Both: the 6 sides around the pair, not the one between them.
    assert.equal(boundaryEdges(pos, [0, 1, 2, 0, 2, 3]).length / 2, 4);
    assert.equal(boundaryEdges(pos, [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]).length / 2, 6);
});

test("the outline follows the warp", () => {
    const mesh = feaMesh();
    open(mesh);
    const overlay = addElementOutlines("p", {elements: [1, 2], throughModel: true});
    assert.ok(overlay);
    const lines = mesh.children[0] as LineSegments2;
    assert.equal(lines.userData.__overlayPass, true); // drawn over the model in the WebGPU viewport too
    const start = lines.geometry.attributes.instanceStart as THREE.InterleavedBufferAttribute;
    const z = () => Array.from(start.data.array as Float32Array)
        .filter((_, i) => i % 3 === 2);
    assert.ok(z().every((v) => v === 0));
    mesh.morphTargetInfluences![0] = 0.5;
    const renderer = {domElement: {clientWidth: 100, clientHeight: 100}} as unknown as THREE.WebGLRenderer;
    const hook = lines.onBeforeRender as unknown as (...args: unknown[]) => void;
    hook.call(lines, renderer, new THREE.Scene(), new THREE.Camera(), lines.geometry, lines.material, new THREE.Group());
    assert.ok(z().every((v) => v === 0.5));
    overlay.remove();
    assert.equal(mesh.children.length, 0);
});

test("no outline for elements the model has not got", () => {
    open(feaMesh());
    assert.equal(addElementOutlines("p", {elements: [99]}), null);
});

test("element paint on the beam solids goes on the beam-solid mesh, and only while it is shown", () => {
    const mesh = feaMesh();
    const solids = feaMesh();
    mesh.add(solids);
    open(mesh, solids);
    const overlay = paintFeaElements("p", {colors: new Map([[2, "#ff0000"]]), on: "beamSolids"});
    assert.ok(overlay);
    assert.equal(solids.children.length, 1);
    overlay.remove();
    solids.visible = false;
    assert.equal(paintFeaElements("p", {colors: new Map([[2, "#ff0000"]]), on: "beamSolids"}), null);
});
