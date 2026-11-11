// How the three.js viewport draws the faces of an FEA result, so a result colour is the legend's.
//
// Two things, both on the mesh's base material:
//
//   * Matte. The bake's GLB names no metalness, and glTF's default is fully metallic: without an
//     environment map such a surface shows only the headlight's weak highlight, about a ninth of
//     its colour. A result is drawn as a matte surface, as the three.js-free viewport draws it.
//   * Flat while painted. Lighting darkens a colour by how its face is turned, and a colour read
//     against a legend must not depend on that. While the vertex colours are on, the material's
//     output is its colour and nothing else, unless the user asked for shaded results
//     (state/appearanceStore.ts). Unpainted, the mesh is lit like any model.
//
// The switch is one uniform shared by every such material, so changing the setting recompiles
// nothing; whether the colours are on is the shader's own USE_COLOR, which follows the
// material's `vertexColors` wherever that is set.

import * as THREE from "three";

import {useAppearanceStore} from "@/state/appearanceStore";
import {requestRender} from "@/state/perfStore";

/** 1 while a painted result is drawn flat, 0 while it is shaded. */
const FLAT = {value: useAppearanceStore.getState().shadeResults ? 0 : 1};
useAppearanceStore.subscribe((s, prev) => {
    if (s.shadeResults === prev.shadeResults) return;
    FLAT.value = s.shadeResults ? 0 : 1;
    requestRender();
});

const LOOK_KEY = "adaResultLook";
/** The materials given the look. Not a mark on the material: a clone would carry the mark without
 *  the shader patch. */
const looked = new WeakSet<THREE.Material>();

/** Whether `material` is a result's base material, drawn flat now (its vertex colours are on and
 *  results are not shaded): what the adacpp mirror of the scene asks. */
export function resultDrawnFlat(material: THREE.Material | undefined): boolean {
    return !!material && FLAT.value === 1 && looked.has(material) && (material as {vertexColors?: boolean}).vertexColors === true;
}

type Lit = THREE.Material & {metalness?: number; roughness?: number; isMeshStandardMaterial?: boolean};

function giveLook(material: THREE.Material): void {
    if (looked.has(material)) return;
    looked.add(material);
    const lit = material as Lit;
    if (lit.isMeshStandardMaterial) {
        lit.metalness = 0;
        lit.roughness = 1;
    }
    material.onBeforeCompile = (shader) => {
        shader.uniforms.adaResultFlat = FLAT;
        shader.fragmentShader = shader.fragmentShader
            .replace("void main() {", "uniform float adaResultFlat;\nvoid main() {")
            .replace(
                /vec3 outgoingLight = ([^;]+);/,
                "vec3 outgoingLight = $1;\n#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA )\n\toutgoingLight = mix( outgoingLight, diffuseColor.rgb, adaResultFlat );\n#endif",
            );
    };
    // Its own program: an unpatched material of the same kind must not be handed this one's.
    material.customProgramCacheKey = () => LOOK_KEY;
    material.needsUpdate = true;
}

/** Give the faces of a result (`object`'s base material) the look above. Does nothing the second
 *  time, and nothing for lines and points, which are unlit as they are. */
export function ensureResultLook(object: THREE.Object3D | null | undefined): void {
    const mesh = object as THREE.Mesh | null | undefined;
    if (!mesh?.isMesh) return;
    // Slot 0 on a CustomBatchedMesh: its other slots are the selection and the hidden ranges.
    const m = mesh.material as THREE.Material | THREE.Material[] | undefined;
    const base = Array.isArray(m) ? m[0] : m;
    const lit = base as (THREE.Material & {isMeshStandardMaterial?: boolean; isMeshLambertMaterial?: boolean; isMeshPhongMaterial?: boolean}) | undefined;
    if (lit && (lit.isMeshStandardMaterial || lit.isMeshLambertMaterial || lit.isMeshPhongMaterial)) giveLook(lit);
}
