import * as THREE from "three";

const RESULT_LINE_SEGMENTS = "__fea_result_line_segments__";

export function clearResultLineSegments(mesh: THREE.Mesh): void {
    const existing = mesh.getObjectByName(RESULT_LINE_SEGMENTS) as THREE.LineSegments | undefined;
    if (!existing) return;
    mesh.remove(existing);
    existing.geometry.dispose();
    const material = existing.material;
    if (Array.isArray(material)) material.forEach((item) => item.dispose());
    else material.dispose();
}

/** Install a result-coloured fallback for line elements without solid faces.
 * Positions, colours and morph deltas contain two duplicated vertices per
 * element, so discontinuous element values never bleed across beam ends. */
export function installResultLineSegments(
    mesh: THREE.Mesh,
    positions: Float32Array,
    colors: Float32Array,
    displacement: Float32Array,
): void {
    clearResultLineSegments(mesh);
    if (positions.length === 0) return;

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geometry.morphAttributes.position = [new THREE.BufferAttribute(displacement, 3)];
    geometry.morphTargetsRelative = true;
    const material = new THREE.LineBasicMaterial({
        vertexColors: true,
        depthTest: true,
    });
    const segments = new THREE.LineSegments(geometry, material);
    segments.name = RESULT_LINE_SEGMENTS;
    segments.renderOrder = 4;
    segments.frustumCulled = false;
    // Result lines are visual only. Element picking continues through the
    // parent mesh/beam-solid draw ranges instead of intercepting raycasts.
    segments.layers.set(1);
    segments.updateMorphTargets();
    segments.onBeforeRender = () => {
        const influence = mesh.morphTargetInfluences?.[0] ?? 0;
        if (segments.morphTargetInfluences) segments.morphTargetInfluences[0] = influence;
    };
    mesh.add(segments);
}
