import * as THREE from "three";

const sourceVertexMaps = new WeakMap<THREE.BufferGeometry, Uint32Array>();

/** Duplicate indexed render vertices once so element-local colours cannot be
 * overwritten by an adjacent element that references the same source node.
 * Draw ranges remain valid because the replacement index is sequential and
 * has exactly the same length as the original index buffer. */
export function ensureElementLocalVertices(
    geometry: THREE.BufferGeometry,
): Uint32Array {
    const cached = sourceVertexMaps.get(geometry);
    if (cached) return cached;
    const index = geometry.getIndex();
    if (!index) {
        throw new Error("Element-local result rendering requires indexed geometry");
    }
    const sourceVertices = Uint32Array.from(index.array as ArrayLike<number>);
    const expanded = geometry.toNonIndexed();

    for (const name of Object.keys(geometry.attributes)) geometry.deleteAttribute(name);
    for (const [name, attribute] of Object.entries(expanded.attributes)) {
        geometry.setAttribute(name, attribute);
    }
    geometry.morphAttributes = expanded.morphAttributes;
    geometry.morphTargetsRelative = expanded.morphTargetsRelative;
    const sequential = sourceVertices.length > 65535
        ? new Uint32Array(sourceVertices.length)
        : new Uint16Array(sourceVertices.length);
    for (let i = 0; i < sequential.length; i++) sequential[i] = i;
    geometry.setIndex(new THREE.BufferAttribute(sequential, 1));
    geometry.boundingBox = null;
    geometry.boundingSphere = null;
    sourceVertexMaps.set(geometry, sourceVertices);
    return sourceVertices;
}

export function sourceVertexIndices(
    geometry: THREE.BufferGeometry,
    nSourceVertices: number,
): Uint32Array {
    const cached = sourceVertexMaps.get(geometry);
    if (cached) return cached;
    const identity = new Uint32Array(nSourceVertices);
    for (let i = 0; i < identity.length; i++) identity[i] = i;
    return identity;
}

export function expandSourceTriples(
    source: Float32Array,
    renderToSource: Uint32Array,
): Float32Array {
    const expanded = new Float32Array(renderToSource.length * 3);
    for (let render = 0; render < renderToSource.length; render++) {
        const sourceOffset = renderToSource[render] * 3;
        const renderOffset = render * 3;
        expanded[renderOffset] = source[sourceOffset];
        expanded[renderOffset + 1] = source[sourceOffset + 1];
        expanded[renderOffset + 2] = source[sourceOffset + 2];
    }
    return expanded;
}
