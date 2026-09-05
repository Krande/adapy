import * as THREE from "three";

const RESULT_POINT_MARKERS = "__fea_result_point_markers__";

/** Show or hide the installed markers without discarding them. Result markers
 * are result colouring, so the colour-visibility toggle routes through this. */
export function setResultPointMarkersVisible(mesh: THREE.Mesh, visible: boolean): void {
    const existing = mesh.getObjectByName(RESULT_POINT_MARKERS);
    if (existing) existing.visible = visible;
}

export function clearResultPointMarkers(mesh: THREE.Mesh): void {
    const existing = mesh.getObjectByName(RESULT_POINT_MARKERS) as THREE.Points | undefined;
    if (!existing) return;
    mesh.remove(existing);
    existing.geometry.dispose();
    const material = existing.material;
    if (Array.isArray(material)) material.forEach((item) => item.dispose());
    else material.dispose();
}

/** Install pixel-sized result markers with the same morph influence as their
 * parent FEA mesh. Positions and displacement are local to that mesh. */
export function installResultPointMarkers(
    mesh: THREE.Mesh,
    positions: Float32Array,
    colors: Float32Array,
    displacement: Float32Array,
): void {
    clearResultPointMarkers(mesh);
    if (positions.length === 0) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geometry.morphAttributes.position = [new THREE.BufferAttribute(displacement, 3)];
    geometry.morphTargetsRelative = true;
    const material = new THREE.PointsMaterial({
        size: 7,
        sizeAttenuation: false,
        vertexColors: true,
        depthTest: true,
    });
    const points = new THREE.Points(geometry, material);
    points.name = RESULT_POINT_MARKERS;
    points.frustumCulled = false;
    points.updateMorphTargets();
    points.onBeforeRender = () => {
        const influence = mesh.morphTargetInfluences?.[0] ?? 0;
        if (points.morphTargetInfluences) points.morphTargetInfluences[0] = influence;
    };
    mesh.add(points);
}
