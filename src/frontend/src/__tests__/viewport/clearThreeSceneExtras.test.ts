// A robot loaded from its URDF (utils/robots.ts) is added to the three.js scene beside the model
// groups, which is all a clear used to take: it stayed in the scene cleared around it.
import assert from "node:assert/strict";
import {test} from "node:test";
import * as THREE from "three";

// Answers vite's `?worker` imports (the scene's helpers import one).
await import("../fixtures/panelRealm");
const {getViewerRuntime} = await import("@/state/viewerRuntime");
const {CLEARED_WITH_MODELS, clearThreeSceneModels} = await import("@/utils/scene/clearThreeSceneModels");

test("a clear of the three.js scene takes what is marked to go with the models, and nothing else", () => {
    const scene = new THREE.Scene();
    const runtime = getViewerRuntime();
    const before = runtime.scene.current;
    runtime.scene.current = scene;
    try {
        const robot = new THREE.Group();
        robot.userData[CLEARED_WITH_MODELS] = true;
        robot.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()));
        const light = new THREE.AmbientLight();
        scene.add(robot, light);
        clearThreeSceneModels();
        assert.deepEqual(scene.children, [light], "the robot went, the light stays");
    } finally {
        runtime.scene.current = before;
    }
});
