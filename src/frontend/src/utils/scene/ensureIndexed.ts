import * as THREE from "three";

/** CustomBatchedMesh addresses what it draws, hides and selects as ranges of the index buffer. A
 *  mesh written without one (every three vertices a triangle) gets the index that says the same. */
export function ensureIndexed(geometry: THREE.BufferGeometry): void {
    const position = geometry.getAttribute("position");
    if (geometry.index || !position) return;
    const count = position.count;
    const index = count > 65535 ? new Uint32Array(count) : new Uint16Array(count);
    for (let i = 0; i < count; i++) index[i] = i;
    geometry.setIndex(new THREE.BufferAttribute(index, 1));
}
