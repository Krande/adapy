// Core's element verbs for plugins: an element set's outline that stays on the warped surface,
// and element paint on the beam solids.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";
import * as THREE from "three";
import type {LineSegments2} from "three/examples/jsm/lines/LineSegments2";

import {useModelSessionStore, type FeaSessionHandle} from "@/state/modelSession";
import {addElementOutlines, addLines, boundaryEdges, paintFeaElements, removeOwnerOverlays} from "@/plugins/sceneOverlays";
// The three.js loader's result as what the verbs act on: registered where the loader is.
import "@/utils/scene/fea/meshOverlayTarget";
import {runBeforeDraw} from "@/viewport/api/beforeDraw";
import {createThreeViewport} from "@/viewport/api/threeViewport";
import {activeViewport, setActiveViewport} from "@/viewport/api/viewportApi";

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

let withdraw: (() => void) | null = null;

/** A three.js viewport on screen: the scene the overlay lines are drawn in. */
function viewport(): THREE.Scene {
    const scene = new THREE.Scene();
    withdraw = setActiveViewport(createThreeViewport({scene, camera: new THREE.PerspectiveCamera(), controls: {} as never}));
    return scene;
}

afterEach(() => {
    removeOwnerOverlays("p");
    useModelSessionStore.getState().close();
    withdraw?.();
    withdraw = null;
});

test("an element set's outline drops the edge its elements share", () => {
    const mesh = feaMesh();
    const pos = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    const rest = (v: number, out: Float32Array, at: number) => void out.set([pos.getX(v), pos.getY(v), pos.getZ(v)], at);
    // One quad: its 4 sides. Both: the 6 sides around the pair, not the one between them.
    assert.equal(boundaryEdges(rest, [0, 1, 2, 0, 2, 3]).length / 2, 4);
    assert.equal(boundaryEdges(rest, [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]).length / 2, 6);
});

test("the outline follows the warp", () => {
    const scene = viewport();
    const mesh = feaMesh();
    mesh.position.set(0, 0, 10);
    open(mesh);
    const overlay = addElementOutlines("p", {elements: [1, 2], throughModel: true});
    assert.ok(overlay);
    const lines = scene.children[0] as LineSegments2;
    assert.equal(lines.userData.__overlayPass, true); // drawn over the model in the WebGPU viewport too
    // In the mesh's own coordinates, standing where the mesh stands.
    assert.equal(lines.matrix.elements[14], 10);
    const start = lines.geometry.attributes.instanceStart as THREE.InterleavedBufferAttribute;
    const z = () => Array.from(start.data.array as Float32Array)
        .filter((_, i) => i % 3 === 2);
    assert.ok(z().every((v) => v === 0));
    mesh.morphTargetInfluences![0] = 0.5;
    // What a viewport's frame loop runs before it draws.
    runBeforeDraw();
    assert.ok(z().every((v) => v === 0.5));
    // A step change rewrites the morph in place and bumps its version.
    const morph = mesh.geometry.morphAttributes.position[0] as THREE.BufferAttribute;
    (morph.array as Float32Array).fill(2);
    morph.needsUpdate = true;
    runBeforeDraw();
    assert.ok(z().every((v) => v === 1));
    overlay.remove();
    assert.equal(scene.children.length, 0);
});

test("no outline for elements the model has not got", () => {
    viewport();
    open(feaMesh());
    assert.equal(addElementOutlines("p", {elements: [99]}), null);
});

test("lines are drawn in linear colour, a colour per segment at both its ends", () => {
    const scene = viewport();
    addLines("p", {segments: [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0], colors: [1, 0, 0, 0.5, 0.5, 0.5]});
    const lines = scene.children[0] as LineSegments2;
    const colors = (lines.geometry.attributes.instanceColorStart as THREE.InterleavedBufferAttribute).data.array as Float32Array;
    const grey = new THREE.Color().setRGB(0.5, 0.5, 0.5, THREE.SRGBColorSpace).r;
    assert.deepEqual(Array.from(colors.slice(0, 6)), [1, 0, 0, 1, 0, 0]);
    assert.ok(Math.abs(colors[6] - grey) < 1e-6 && Math.abs(colors[11] - grey) < 1e-6);
    addLines("p", {segments: [0, 0, 0, 1, 0, 0], color: "#ff8000"});
    const one = (scene.children[1] as LineSegments2).material.color;
    assert.ok(Math.abs(one.r - new THREE.Color("#ff8000").r) < 1e-6 && Math.abs(one.g - new THREE.Color("#ff8000").g) < 1e-6);
    removeOwnerOverlays("p");
    assert.equal(scene.children.length, 0);
});

test("a shape of an overlay is an unlit mesh that no click or fit takes for the model", () => {
    const scene = viewport();
    const layer = activeViewport()!.overlays("shapes");
    layer.addMesh({
        positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), indices: new Uint32Array([0, 1, 2]),
        colors: new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1]), opacity: 0.5, onTop: true, fitted: false,
        matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 7, 1],
    });
    const mesh = scene.children[0] as THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    assert.ok(mesh.isMesh && mesh.material.isMeshBasicMaterial);
    assert.equal(mesh.material.vertexColors, true);
    assert.equal(mesh.material.depthTest, false);
    assert.equal(mesh.material.opacity, 0.5);
    assert.equal(mesh.userData.__overlayPass, true);
    assert.equal(mesh.userData.__excludeFromFit, true);
    assert.equal(mesh.matrix.elements[14], 7);
    assert.equal(mesh.geometry.index!.count, 3);
    // A ray through it hits nothing.
    const hits: THREE.Intersection[] = [];
    mesh.raycast(new THREE.Raycaster(new THREE.Vector3(0.2, 0.2, 20), new THREE.Vector3(0, 0, -1)), hits);
    assert.equal(hits.length, 0);
    layer.dispose();
    assert.equal(scene.children.length, 0);
});

test("element paint on the beam solids goes on the beam-solid mesh, and only while it is shown", () => {
    const mesh = feaMesh();
    const solids = feaMesh();
    mesh.add(solids);
    open(mesh, solids);
    const overlay = paintFeaElements("p", {colors: new Map([[2, "#ff0000"]]), on: "beamSolids"});
    assert.ok(overlay);
    assert.equal(solids.children.length, 1);
    // Element 2's corners are red, element 1's triangles collapsed to nothing.
    const painted = solids.children[0] as THREE.Mesh;
    const color = painted.geometry.getAttribute("color").array as Float32Array;
    assert.deepEqual(Array.from(color.slice(18, 21)), [1, 0, 0]);
    const position = painted.geometry.getAttribute("position").array as Float32Array;
    assert.deepEqual(Array.from(position.slice(0, 9)), [0, 0, 0, 0, 0, 0, 0, 0, 0]);
    overlay.remove();
    assert.equal(solids.children.length, 0);
    solids.visible = false;
    assert.equal(paintFeaElements("p", {colors: new Map([[2, "#ff0000"]]), on: "beamSolids"}), null);
});
