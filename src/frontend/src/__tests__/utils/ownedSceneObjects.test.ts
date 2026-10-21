// Objects a mount adds to the long-lived viewer scene: a rerun replaces them, never stacks them.
import assert from "node:assert/strict";
import {test} from "node:test";
import * as THREE from "three";

import {claimSceneObjects, ownedBy} from "../../utils/scene/ownedSceneObjects";
import {setupLights} from "../../components/viewer/sceneHelpers/setupLights";

const lights = (scene: THREE.Scene) => scene.children.filter((o) => (o as THREE.Light).isLight);

test("rerunning the lighting setup replaces the lights instead of adding more", () => {
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    // A remount whose cleanup never ran, then two that clean up in the usual order.
    setupLights(claimSceneObjects(scene, "canvas"), camera);
    const second = claimSceneObjects(scene, "canvas");
    setupLights(second, camera);
    assert.equal(lights(scene).length, 2); // the ambient and the camera light, once
    second.dispose();
    setupLights(claimSceneObjects(scene, "canvas"), camera);
    assert.equal(lights(scene).length, 2);
    assert.equal(scene.children.length, 3); // and the camera light's target
});

test("dispose removes only what its own claim added", () => {
    const scene = new THREE.Scene();
    const older = claimSceneObjects(scene, "canvas");
    older.add(new THREE.Object3D());
    const newer = claimSceneObjects(scene, "canvas");
    const kept = newer.add(new THREE.Object3D());
    older.dispose(); // a late cleanup of the older mount
    assert.deepEqual(ownedBy(scene, "canvas"), [kept]);
});

test("owners leave each other's objects and unowned ones alone", () => {
    const scene = new THREE.Scene();
    const model = new THREE.Group();
    scene.add(model);
    claimSceneObjects(scene, "a").add(new THREE.Object3D());
    claimSceneObjects(scene, "b").add(new THREE.Object3D());
    claimSceneObjects(scene, "a");
    assert.equal(ownedBy(scene, "a").length, 0);
    assert.equal(ownedBy(scene, "b").length, 1);
    assert.ok(scene.children.includes(model));
});
