// The appearance settings on the three.js scene, and the look of an FEA result's faces there: the
// lights and the background follow the settings without anything being rebuilt, geometry without
// a colour takes the default colour, and a painted result is matte and drawn flat.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";
import * as THREE from "three";

import {useAppearanceStore} from "@/state/appearanceStore";
import {setupLights} from "@/components/viewer/sceneHelpers/setupLights";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {DEFAULT_BASE_COLOR, defaultBlackColor, hasDefaultColor} from "@/utils/scene/defaultBlackColor";
import {ensureResultLook, resultDrawnFlat} from "@/utils/scene/fea/threeResultLook";
import {claimSceneObjects} from "@/utils/scene/ownedSceneObjects";
import {applyDefaultColor, bindThreeAppearance} from "@/utils/scene/threeAppearance";

const settings = () => useAppearanceStore.getState();
beforeEach(() => settings().reset());

const lightsOf = (scene: THREE.Scene) => ({
    ambient: scene.children.find((o) => (o as THREE.AmbientLight).isAmbientLight) as THREE.AmbientLight,
    headlight: scene.children.find((o) => (o as THREE.DirectionalLight).isDirectionalLight) as THREE.DirectionalLight,
});

test("the lights start as the settings say and follow them: two numbers, no new light", () => {
    const scene = new THREE.Scene();
    const {release} = setupLights(claimSceneObjects(scene, "test"), new THREE.PerspectiveCamera());
    const {ambient, headlight} = lightsOf(scene);
    assert.equal(ambient.intensity, Math.PI / 2);
    assert.equal(headlight.intensity, 1.4);
    const count = scene.children.length;
    settings().setBrightness(0.5);
    assert.ok(Math.abs(ambient.intensity - Math.PI / 4) < 1e-9);
    assert.ok(Math.abs(headlight.intensity - 0.7) < 1e-9);
    settings().setAmbientShare(1);
    assert.equal(headlight.intensity, 0);
    assert.equal(scene.children.length, count);
    release();
    settings().reset();
    assert.equal(headlight.intensity, 0, "released: no longer following");
});

test("the background follows the setting", () => {
    const scene = new THREE.Scene();
    const release = bindThreeAppearance(scene);
    assert.equal((scene.background as THREE.Color).getHexString(), "393939");
    settings().setBackground("#102030");
    assert.equal((scene.background as THREE.Color).getHexString(), "102030");
    release();
});

function batched(material: THREE.Material): CustomBatchedMesh {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array([0, 1, 2]), 1));
    return new CustomBatchedMesh(g, material, new Map([["a", [0, 3]]]), "m", true, null);
}

test("geometry without a colour is drawn in the default colour, and follows it without a reload", () => {
    const grey = new THREE.MeshStandardMaterial({color: new THREE.Color(DEFAULT_BASE_COLOR, DEFAULT_BASE_COLOR, DEFAULT_BASE_COLOR)});
    const black = new THREE.MeshStandardMaterial({color: 0x000000});
    const red = new THREE.MeshStandardMaterial({color: 0xff0000});
    assert.equal(defaultBlackColor(grey), true, "the grey ada writes for an object without a colour");
    assert.equal(defaultBlackColor(black), true);
    assert.equal(defaultBlackColor(red), false);
    assert.deepEqual(black.color.toArray().map((c) => c.toFixed(3)), ["0.800", "0.800", "0.800"]);
    assert.equal(hasDefaultColor(black.clone()), true, "a clone knows too: a batched mesh draws with clones");

    const scene = new THREE.Scene();
    const plain = batched(grey), coloured = batched(red);
    scene.add(plain, coloured);
    const release = bindThreeAppearance(scene);
    settings().setDefaultColor("#ff0000");
    assert.deepEqual((plain.originalMaterial as THREE.MeshStandardMaterial).color.toArray(), [1, 0, 0]);
    assert.equal((coloured.originalMaterial as THREE.MeshStandardMaterial).color.getHexString(), "ff0000");
    // Under colours the user gave objects, the mesh's own colour is per vertex: it follows too.
    plain.setUserColors(null, 0x00ff00);
    plain.setUserColors(["a"], null);
    settings().setDefaultColor("#0000ff");
    plain.setUserColors(["a"], 0x00ff00);
    plain.setUserColors(null, null);
    assert.deepEqual((plain.originalMaterial as THREE.MeshStandardMaterial).color.toArray(), [0, 0, 1]);
    settings().reset();
    applyDefaultColor(scene);
    assert.deepEqual((plain.originalMaterial as THREE.MeshStandardMaterial).color.toArray().map((c) => c.toFixed(3)), ["0.800", "0.800", "0.800"]);
    release();
});

test("a result's faces are matte, and flat while painted unless results are shaded", () => {
    // The bake's GLB names no metalness: glTF's default is fully metallic.
    const mesh = batched(new THREE.MeshStandardMaterial({metalness: 1, roughness: 1}));
    ensureResultLook(mesh);
    const material = mesh.originalMaterial as THREE.MeshStandardMaterial;
    assert.equal(material.metalness, 0);
    assert.equal(material.roughness, 1);
    assert.equal(resultDrawnFlat(material), false, "unpainted: lit like a model");
    material.vertexColors = true;
    assert.equal(resultDrawnFlat(material), true);
    settings().setShadeResults(true);
    assert.equal(resultDrawnFlat(material), false);
    assert.equal(resultDrawnFlat(new THREE.MeshStandardMaterial({vertexColors: true})), false, "not a result's material");

    // The shader: the lit colour is replaced by the colour itself, by one uniform, where colours are on.
    const shader = {uniforms: {} as Record<string, {value: unknown}>, fragmentShader: THREE.ShaderLib.standard.fragmentShader, vertexShader: ""};
    material.onBeforeCompile(shader as never, null as never);
    assert.match(shader.fragmentShader, /uniform float adaResultFlat;/);
    assert.match(shader.fragmentShader, /outgoingLight = mix\( outgoingLight, diffuseColor\.rgb, adaResultFlat \);/);
    assert.equal(shader.uniforms.adaResultFlat.value, 0);
    settings().setShadeResults(false);
    assert.equal(shader.uniforms.adaResultFlat.value, 1, "the setting is the uniform: nothing recompiles");
    assert.notEqual(material.customProgramCacheKey(), new THREE.MeshStandardMaterial().customProgramCacheKey());

    // Lambert (the performance option) and Phong take the same patch.
    for (const lib of [THREE.ShaderLib.lambert, THREE.ShaderLib.phong]) {
        const other = {uniforms: {}, fragmentShader: lib.fragmentShader, vertexShader: ""};
        material.onBeforeCompile(other as never, null as never);
        assert.match(other.fragmentShader, /mix\( outgoingLight, diffuseColor\.rgb, adaResultFlat \)/);
    }
});
