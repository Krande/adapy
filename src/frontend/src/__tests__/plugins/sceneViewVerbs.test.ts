// Core's view verbs for plugins (1.10.0): holding the FEA model undeformed beside the user's warp
// settings, and framing the camera on elements where they are drawn.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";
import * as THREE from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useModelSessionStore, type FeaSessionHandle} from "@/state/modelSession";
import {createViewerRuntime, mountViewerRuntime} from "@/state/viewerRuntime";
import {frameFeaElements, frameSceneBox} from "@/plugins/sceneOverlays";
import {
    _resetFeaDeformationGate,
    applyFeaDeformationGate,
    feaDeformationShown,
    releaseFeaDeformation,
    setFeaDeformationShown,
} from "@/utils/scene/fea/deformationGate";

/** Element 1 a unit quad at x 0..1, element 2 one at x 10..11; a relative morph lifting every vertex 5 in z. */
function feaMesh(): THREE.Mesh & {drawRanges: Map<string, [number, number]>} {
    const quad = (x: number) => [x, 0, 0, x + 1, 0, 0, x + 1, 1, 0, x, 1, 0];
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.Float32BufferAttribute([...quad(0), ...quad(10)], 3));
    geom.setIndex([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]);
    geom.morphAttributes.position = [new THREE.Float32BufferAttribute(new Array(8).fill([0, 0, 5]).flat(), 3)];
    geom.morphTargetsRelative = true;
    const mesh = new THREE.Mesh(geom) as THREE.Mesh & {drawRanges: Map<string, [number, number]>};
    mesh.morphTargetInfluences = [1];
    mesh.drawRanges = new Map([["E1", [0, 6]], ["E2", [6, 6]]]);
    return mesh;
}

function open(mesh: THREE.Mesh): void {
    const session = useModelSessionStore.getState().open({sourceName: "m.SIN", kind: "fea"});
    session.fea = {sourceName: "m.SIN", mesh} as unknown as FeaSessionHandle;
}

let unmount: (() => void) | null = null;

/** A mounted viewer with an orbit camera, as the canvas sets one up. */
function viewer(): {camera: THREE.PerspectiveCamera; controls: OrbitControls} {
    const runtime = createViewerRuntime();
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000);
    camera.position.set(0, 0, 50);
    // An OrbitControls without a DOM to listen on: the fit only sets its target and calls update().
    const controls = Object.assign(Object.create(OrbitControls.prototype), {
        object: camera,
        target: new THREE.Vector3(),
        update() {},
    }) as OrbitControls;
    (runtime.camera as {current: unknown}).current = camera;
    (runtime.controls as {current: unknown}).current = controls;
    unmount = mountViewerRuntime(runtime);
    return {camera, controls};
}

afterEach(() => {
    _resetFeaDeformationGate();
    useModelSessionStore.getState().close();
    unmount?.();
    unmount = null;
});

test("a held model is drawn undeformed, and the user's warp comes back when it is let go", () => {
    const mesh = feaMesh();
    open(mesh);
    useFeaAnimationStore.setState({factor: 0.5, scaleFactor: 4});

    setFeaDeformationShown("shell", false);
    applyFeaDeformationGate();
    assert.equal(mesh.morphTargetInfluences![0], 0);
    assert.equal(feaDeformationShown(), false);

    // Anything else writing the warp between frames is overruled at the next one.
    mesh.morphTargetInfluences![0] = 3;
    applyFeaDeformationGate();
    assert.equal(mesh.morphTargetInfluences![0], 0);

    setFeaDeformationShown("shell", true);
    applyFeaDeformationGate();
    assert.equal(mesh.morphTargetInfluences![0], 2); // factor x scale
    assert.equal(feaDeformationShown(), true);

    // Nobody holding it: the gate leaves the warp to its writers.
    mesh.morphTargetInfluences![0] = 3;
    applyFeaDeformationGate();
    assert.equal(mesh.morphTargetInfluences![0], 3);
});

test("the model stays undeformed while any owner holds it, and a removed owner lets go", () => {
    const mesh = feaMesh();
    open(mesh);
    setFeaDeformationShown("a", false);
    setFeaDeformationShown("b", false);
    setFeaDeformationShown("a", true);
    applyFeaDeformationGate();
    assert.equal(mesh.morphTargetInfluences![0], 0);
    releaseFeaDeformation("b");
    assert.equal(feaDeformationShown(), true);
});

test("framing elements aims at where they are drawn, warp included", () => {
    open(feaMesh());
    const {controls} = viewer();
    assert.equal(frameFeaElements([2]), true);
    // Element 2 spans x 10..11, y 0..1, lifted 5 in z by the warp.
    assert.ok(controls.target.distanceTo(new THREE.Vector3(10.5, 0.5, 5)) < 1e-6, controls.target.toArray().join());
    assert.equal(frameFeaElements([99]), false);
});

test("framing a box aims at its centre; without a viewer nothing is framed", () => {
    assert.equal(frameSceneBox({min: [0, 0, 0], max: [2, 4, 6]}), false);
    const {controls} = viewer();
    assert.equal(frameSceneBox({min: [0, 0, 0], max: [2, 4, 6]}), true);
    assert.deepEqual(controls.target.toArray(), [1, 2, 3]);
});

// ----- 1.11.0 --------------------------------------------------------------

test("the unpainted model takes the asked colour, and its own back after", async () => {
    const {applyFeaUnpaintedColor, setFeaUnpaintedColor, _resetFeaUnpaintedColor} = await import(
        "@/utils/scene/fea/unpaintedColor"
    );
    const mesh = feaMesh();
    const mat = new THREE.MeshStandardMaterial({color: 0x808080});
    mesh.material = mat;
    open(mesh);
    const own = mat.color.clone();

    setFeaUnpaintedColor("shell", "#ff0000");
    applyFeaUnpaintedColor();
    assert.ok(mat.color.equals(new THREE.Color("#ff0000")));

    // A paint (per-vertex colours) draws over it: the base colour is given back while it shows.
    mat.vertexColors = true;
    applyFeaUnpaintedColor();
    assert.ok(mat.color.equals(own));
    mat.vertexColors = false;
    applyFeaUnpaintedColor();
    assert.ok(mat.color.equals(new THREE.Color("#ff0000")));

    setFeaUnpaintedColor("shell", null);
    applyFeaUnpaintedColor();
    assert.ok(mat.color.equals(own));
    _resetFeaUnpaintedColor();
});

test("a label is cut at its anchor, whole, not per pixel", async () => {
    const {anchorClipVertexShader} = await import("@/plugins/sceneOverlays");
    const patched = anchorClipVertexShader(THREE.ShaderLib.sprite.vertexShader);
    assert.ok(!patched.includes("#include <clipping_planes_vertex>"));
    assert.ok(patched.includes("vClipPosition = - modelViewMatrix[ 3 ].xyz;"));
});
