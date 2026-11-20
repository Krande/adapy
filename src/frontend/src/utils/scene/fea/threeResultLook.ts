// How the three.js viewport draws the faces of an FEA result, so a result colour is the legend's.
//
// Two things, both on the mesh's base material:
//
//   * Matte. The bake's GLB names no metalness, and glTF's default is fully metallic: without an
//     environment map such a surface shows only the headlight's weak highlight, about a ninth of
//     its colour. A result is drawn as a matte surface, as the three.js-free viewport draws it.
//   * Its colours the legend's while painted. Flat by default: the material's output is its colour
//     and nothing else. Shaded when the user asks (state/appearanceStore.ts): the colour darkened by
//     the diffuse light on its face and nothing more -- no highlight, no channel clipped -- so the
//     hue and saturation stay the legend's (resultShading.ts has the numbers). Unpainted, the mesh
//     is lit like any model.
//
// The switches are uniforms shared by every such material, so changing a setting recompiles
// nothing; whether the colours are on is the shader's own USE_COLOR, which follows the
// material's `vertexColors` wherever that is set.

import * as THREE from "three";

import {useAppearanceStore} from "@/state/appearanceStore";
import {requestRender} from "@/state/perfStore";
import {resultExposure} from "./resultShading";

/** 1 while a painted result is drawn flat, 0 while it is shaded. */
const FLAT = {value: useAppearanceStore.getState().shadeResults ? 0 : 1};
/** What the lights are scaled by on a shaded result (resultShading.ts). */
const EXPOSURE = {value: resultExposure()};
useAppearanceStore.subscribe((s, prev) => {
    if (s.shadeResults === prev.shadeResults && s.brightness === prev.brightness && s.ambientShare === prev.ambientShare) return;
    FLAT.value = s.shadeResults ? 0 : 1;
    EXPOSURE.value = resultExposure(s);
    requestRender();
});

const LOOK_KEY = "adaResultLook";
/** The materials given the look. Not a mark on the material: a clone would carry the mark without
 *  the shader patch. */
const looked = new WeakSet<THREE.Material>();

/** Whether `material` is a result's base material with its vertex colours on. */
function painted(material: THREE.Material | undefined): material is THREE.Material {
    return !!material && looked.has(material) && (material as {vertexColors?: boolean}).vertexColors === true;
}

/** Whether `material` is a result's base material, drawn flat now (its vertex colours are on and
 *  results are not shaded): what the adacpp mirror of the scene asks. */
export function resultDrawnFlat(material: THREE.Material | undefined): boolean {
    return FLAT.value === 1 && painted(material);
}

/** For a result's base material shaded now (its vertex colours on, results shaded): the factor its
 *  lights are scaled by, which the adacpp mirror draws as the instance's colour, with no highlight.
 *  Null for anything else. */
export function resultShadedExposure(material: THREE.Material | undefined): number | null {
    return FLAT.value === 0 && painted(material) ? EXPOSURE.value : null;
}

type Lit = THREE.Material & {metalness?: number; roughness?: number; isMeshStandardMaterial?: boolean};

/** The fragment code put after the material's `outgoingLight`: the painted colour, flat or darkened by
 *  the diffuse light alone. `irradiance` is the ambient light (lights_fragment_begin); the direct
 *  diffuse is the colour times the headlight's N.L / PI, so its ratio to the colour is that factor. */
const SHADE_GLSL = `
#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA )
#ifdef RE_IndirectDiffuse
	float adaLight = dot( irradiance, vec3( 1.0 / 3.0 ) ) * RECIPROCAL_PI
		+ dot( reflectedLight.directDiffuse, vec3( 1.0 ) ) / max( dot( diffuseColor.rgb, vec3( 1.0 ) ), 1e-6 );
	vec3 adaShaded = diffuseColor.rgb * min( adaLight * adaResultExposure, 1.0 );
#else
	vec3 adaShaded = outgoingLight;
#endif
	outgoingLight = mix( adaShaded, diffuseColor.rgb, adaResultFlat );
#endif`;

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
        shader.uniforms.adaResultExposure = EXPOSURE;
        shader.fragmentShader = shader.fragmentShader
            .replace("void main() {", "uniform float adaResultFlat;\nuniform float adaResultExposure;\nvoid main() {")
            .replace(/vec3 outgoingLight = ([^;]+);/, `vec3 outgoingLight = $1;${SHADE_GLSL}`);
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
