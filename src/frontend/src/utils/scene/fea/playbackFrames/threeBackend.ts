// Recorded-frame displacement for the three.js (WebGL2) renderer.
//
// three.js can only get new morph data to the GPU by rebuilding the geometry's
// morph texture from scratch (WebGLMorphtargets bakes it once per morph-target
// count): a JS loop over every render vertex, a fresh buffer, a full upload. On a
// 1M-render-vertex shell model that was most of every played frame.
//
// Instead each recorded frame's displacement is one layer of a half-float array
// texture, in SOURCE vertex numbering, and the result mesh's vertex shader reads its
// own vertex's displacement from the current layer:
//
//     transformed = position + morphTargetInfluences[0] * disp(layer, source vertex)
//
// Showing a frame sets the layer uniform. The scale is the mesh's own morph
// influence, so the warp-scale knob, the warp toggle and the deformation gate need
// no wiring. An element-local geometry (an element field expands each element to its
// own vertices) carries its render→source map as the `feaSrc` attribute; a nodal
// geometry and the element-edge wireframe are in source numbering already and use
// gl_VertexID.

import * as THREE from "three";

import {elementLocalMap} from "../elementLocalGeometry";
import type {FrameDisplacementBackend} from "./backend";

/** Texture width in texels. WebGL2 guarantees 2048. */
const TEX_WIDTH = 2048;
/** WebGL2 guarantees 256 array layers. */
export const THREE_MAX_FRAMES = 256;

/** The uniforms every patched material shares. One set for the viewer: a compiled
 *  program holds these objects, so a new recording swaps the texture in
 *  `feaDispTex.value` rather than replacing them. */
const UNIFORMS = {
    feaDispTex: {value: null as THREE.DataArrayTexture | null},
    feaDispWidth: {value: TEX_WIDTH},
    feaFrameLayer: {value: 0},
    feaFramesOn: {value: 0},
};

const patched = new WeakSet<THREE.Material>();

const PARS = (mapped: boolean) => `
uniform highp sampler2DArray feaDispTex;
uniform int feaDispWidth;
uniform int feaFrameLayer;
uniform float feaFramesOn;
${mapped ? "attribute float feaSrc;" : ""}
`;

const MAIN = (mapped: boolean) => `
if ( feaFramesOn > 0.5 ) {
    int feaIdx = ${mapped ? "int( feaSrc + 0.5 )" : "gl_VertexID"};
    ivec3 feaUv = ivec3( feaIdx % feaDispWidth, feaIdx / feaDispWidth, feaFrameLayer );
    float feaK = 0.0;
    #if defined( USE_MORPHTARGETS ) && !defined( USE_INSTANCING_MORPH )
        feaK = morphTargetInfluences[ 0 ];
    #endif
    transformed = vec3( position ) + feaK * texelFetch( feaDispTex, feaUv, 0 ).xyz;
}
`;

/** The vertex-shader edit. A shader without the morph chunk is left alone. */
export function patchVertexShader(source: string, mapped: boolean): string {
    if (!source.includes("#include <morphtarget_vertex>")) return source;
    return source
        .replace("#include <common>", `#include <common>\n${PARS(mapped)}`)
        .replace("#include <morphtarget_vertex>", `#include <morphtarget_vertex>\n${MAIN(mapped)}`);
}

/** Teach `material` to read the recorded displacement (see the module comment for
 *  `mapped`). Chains any existing `onBeforeCompile`. Idempotent. */
export function patchFrameMaterial(material: THREE.Material, mapped: boolean): void {
    if (patched.has(material)) return;
    patched.add(material);
    const previous = material.onBeforeCompile?.bind(material);
    const previousKey = material.customProgramCacheKey?.bind(material);
    material.onBeforeCompile = (shader, renderer) => {
        previous?.(shader, renderer);
        Object.assign(shader.uniforms, UNIFORMS);
        shader.vertexShader = patchVertexShader(shader.vertexShader, mapped);
    };
    material.customProgramCacheKey = () => `${previousKey?.() ?? ""}|feaFrames:${mapped ? 1 : 0}`;
    material.needsUpdate = true;
}

function materialsOf(object: THREE.Object3D): THREE.Material[] {
    const m = (object as THREE.Mesh).material;
    if (!m) return [];
    return Array.isArray(m) ? m : [m];
}

export class ThreeFrameBackend implements FrameDisplacementBackend {
    readonly kind = "three" as const;
    private texture: THREE.DataArrayTexture | null = null;
    private data: Uint16Array | null = null;
    private texHeight = 1;
    private nSource = 0;

    displacementBytes(nSource: number): number {
        return TEX_WIDTH * Math.max(1, Math.ceil(nSource / TEX_WIDTH)) * 8;
    }

    allocate(nSource: number, capacity: number): void {
        this.nSource = nSource;
        this.texHeight = Math.max(1, Math.ceil(nSource / TEX_WIDTH));
        const layers = Math.max(1, Math.min(capacity, THREE_MAX_FRAMES));
        this.data = new Uint16Array(TEX_WIDTH * this.texHeight * 4 * layers);
        const texture = new THREE.DataArrayTexture(this.data, TEX_WIDTH, this.texHeight, layers);
        texture.format = THREE.RGBAFormat;
        texture.type = THREE.HalfFloatType;
        texture.minFilter = THREE.NearestFilter;
        texture.magFilter = THREE.NearestFilter;
        texture.generateMipmaps = false;
        texture.needsUpdate = true;
        this.texture = texture;
    }

    store(slot: number, sourceDisplacement: Float32Array): void {
        if (!this.data || !this.texture) return;
        const base = slot * TEX_WIDTH * this.texHeight * 4;
        const toHalf = THREE.DataUtils.toHalfFloat;
        for (let v = 0; v < this.nSource; v++) {
            const o = base + v * 4;
            const s = v * 3;
            this.data[o] = toHalf(sourceDisplacement[s]);
            this.data[o + 1] = toHalf(sourceDisplacement[s + 1]);
            this.data[o + 2] = toHalf(sourceDisplacement[s + 2]);
        }
        // Only this layer goes up, not the whole stack.
        this.texture.addLayerUpdate(slot);
        this.texture.needsUpdate = true;
    }

    present(slot: number, mesh: THREE.Mesh, edges: THREE.LineSegments | null): void {
        if (!this.texture) return;
        const geometry = mesh.geometry;
        const nRender = geometry.getAttribute("position")?.count ?? 0;
        const src = geometry.getAttribute("feaSrc");
        if (!src || src.count !== nRender) {
            const map = elementLocalMap(geometry);
            const values = new Float32Array(nRender);
            for (let i = 0; i < nRender; i++) values[i] = map ? map[i] : i;
            geometry.setAttribute("feaSrc", new THREE.BufferAttribute(values, 1));
        }
        for (const m of materialsOf(mesh)) patchFrameMaterial(m, true);
        if (edges) for (const m of materialsOf(edges)) patchFrameMaterial(m, false);
        UNIFORMS.feaDispTex.value = this.texture;
        UNIFORMS.feaFrameLayer.value = slot;
        UNIFORMS.feaFramesOn.value = 1;
    }

    release(): void {
        UNIFORMS.feaFramesOn.value = 0;
    }

    dispose(): void {
        if (this.texture && UNIFORMS.feaDispTex.value === this.texture) {
            UNIFORMS.feaFramesOn.value = 0;
            UNIFORMS.feaDispTex.value = null;
        }
        this.texture?.dispose();
        this.texture = null;
        this.data = null;
    }
}

/** For tests: whether a patched shader is currently reading the frames. */
export function threeFramesPresenting(): boolean {
    return UNIFORMS.feaFramesOn.value > 0.5;
}
