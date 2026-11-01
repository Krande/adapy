// FEA repaints without a full GPU re-upload.
//
// three.js (r176) bakes a geometry's morph targets into a DataArrayTexture once
// and only rebuilds it when the morph target COUNT changes (WebGLMorphtargets:
// `entry.count !== morphTargetsCount`). A step change keeps the count, so the
// repaint code used to fire the geometry's "dispose" event to drop that texture.
// That event is heavy-handed: WebGLGeometries answers it by deleting the GPU
// buffer of EVERY attribute on the geometry -- index, position, normal, colour --
// so each step change re-uploaded the whole mesh. On a 255k-node transient deck
// played at 12 fps that is the stutter: tens of MB of buffer uploads per frame,
// for a change that only touched the morph delta and the colours.
//
// `refreshMorphTexture` fires the same event with the attributes detached, so
// only what the event owns besides them goes: the morph texture, the vertex-array
// bindings and the wireframe index. The attribute buffers stay on the GPU; the
// ones whose values did change (colour, a reset position) carry `needsUpdate`
// and go up as a sub-upload into the buffer that is already there.

import * as THREE from "three";

/** Drop `geometry`'s cached morph texture (and its VAO bindings) while keeping
 *  every attribute's GPU buffer. The next render rebuilds the texture from the
 *  geometry's current `morphAttributes`. Safe on a geometry no renderer has seen:
 *  nobody listens, nothing happens. */
export function refreshMorphTexture(geometry: THREE.BufferGeometry): void {
    const attributes = geometry.attributes;
    const index = geometry.index;
    // Detached for the duration of the event only: WebGLGeometries walks
    // `geometry.attributes` and `geometry.index` to decide which buffers to delete.
    geometry.attributes = {};
    geometry.index = null;
    try {
        geometry.dispatchEvent({type: "dispose"});
    } finally {
        geometry.attributes = attributes;
        geometry.index = index;
    }
}

/**
 * Write `source` (one xyz triple per SOURCE vertex) into the geometry's morph
 * target 0, expanded through `renderToSource` (null = identity), then refresh the
 * morph texture.
 *
 * Reuses the installed morph attribute when it already has the right length: the
 * data is copied in place and the attribute's version bumped (the WebGPU scene
 * mirror tracks morphs by identity + version). The morph ARRAY object is kept as
 * well, so wireframe children that share it (linkLineMorphToMesh) see the new data
 * without being re-linked. A missing or wrong-length morph gets a fresh attribute.
 */
export function writeMorphTarget(
    geometry: THREE.BufferGeometry,
    source: Float32Array,
    renderToSource: Uint32Array | null,
): void {
    const length = renderToSource ? renderToSource.length * 3 : source.length;
    const existing = geometry.morphAttributes.position?.[0] as THREE.BufferAttribute | undefined;
    let target: Float32Array;
    if (
        existing
        && existing.itemSize === 3
        && existing.array instanceof Float32Array
        && existing.array.length === length
    ) {
        target = existing.array;
        fillExpanded(target, source, renderToSource);
        existing.needsUpdate = true;
    } else {
        target = new Float32Array(length);
        fillExpanded(target, source, renderToSource);
        geometry.morphAttributes.position = [new THREE.BufferAttribute(target, 3)];
    }
    geometry.morphTargetsRelative = true;
    refreshMorphTexture(geometry);
}

/** Write per-source-vertex RGB triples into the geometry's colour attribute,
 *  expanded through `renderToSource` (null = identity). In place when the
 *  attribute fits, a new attribute otherwise. */
export function writeColorAttribute(
    geometry: THREE.BufferGeometry,
    source: Float32Array,
    renderToSource: Uint32Array | null,
): void {
    const length = renderToSource ? renderToSource.length * 3 : source.length;
    const existing = geometry.getAttribute("color") as THREE.BufferAttribute | undefined;
    if (
        existing
        && existing.itemSize === 3
        && existing.array instanceof Float32Array
        && existing.array.length === length
    ) {
        fillExpanded(existing.array, source, renderToSource);
        existing.needsUpdate = true;
        return;
    }
    const out = new Float32Array(length);
    fillExpanded(out, source, renderToSource);
    geometry.setAttribute("color", new THREE.BufferAttribute(out, 3));
}

function fillExpanded(out: Float32Array, source: Float32Array, renderToSource: Uint32Array | null): void {
    if (!renderToSource) {
        out.set(source.length === out.length ? source : source.subarray(0, out.length));
        return;
    }
    for (let render = 0; render < renderToSource.length; render++) {
        const s = renderToSource[render] * 3;
        const r = render * 3;
        out[r] = source[s];
        out[r + 1] = source[s + 1];
        out[r + 2] = source[s + 2];
    }
}

// The position attribute each geometry last had its normals computed for, and the
// base positions it was last reset to. Both only change when the geometry is
// rebuilt (a new source) or expanded to element-local vertices (a new position
// attribute), never on a step change.
const normalsComputedFor = new WeakMap<THREE.BufferGeometry, THREE.BufferAttribute | THREE.InterleavedBufferAttribute>();
const baseResetFor = new WeakMap<THREE.BufferGeometry, {attr: unknown; base: Float32Array}>();

/**
 * `computeVertexNormals`, but only when the position attribute changed since the
 * last time. The FEA repaint resets positions to the undeformed base and carries
 * the deformation in the morph delta, so the normals it computes are the same on
 * every step: recomputing them each frame was pure cost.
 */
export function ensureVertexNormals(geometry: THREE.BufferGeometry): void {
    const position = geometry.getAttribute("position");
    if (!position) return;
    if (normalsComputedFor.get(geometry) === position && geometry.getAttribute("normal")) return;
    geometry.computeVertexNormals();
    normalsComputedFor.set(geometry, position);
}

/**
 * Reset the position attribute to `base` (source numbering, expanded through
 * `renderToSource`) unless it already holds exactly that. Repeated repaints of the
 * same source skip both the copy and the upload.
 */
export function resetBasePositions(
    geometry: THREE.BufferGeometry,
    base: Float32Array,
    renderToSource: Uint32Array | null,
): void {
    const posAttr = geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!posAttr || !(posAttr.array instanceof Float32Array)) return;
    const last = baseResetFor.get(geometry);
    if (last && last.attr === posAttr && last.base === base) return;
    const length = renderToSource ? renderToSource.length * 3 : base.length;
    if (posAttr.array.length !== length) return;
    fillExpanded(posAttr.array, base, renderToSource);
    posAttr.needsUpdate = true;
    baseResetFor.set(geometry, {attr: posAttr, base});
}
