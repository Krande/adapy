import * as THREE from "three";

// The element-edge wireframes and the vertex numbering they live in.
//
// A wireframe overlay SHARES its parent mesh's position BufferAttribute and
// carries an index written against the bake's original ("source") vertices.
// Painting an element field expands the parent geometry to element-local
// vertices and SWAPS its position attribute for a longer one — the overlay
// keeps the old attribute, which is exactly right, because its index still
// refers to those vertices.
//
// What it must not keep is the parent's morph. That one is expanded to match
// the parent's new position buffer, and three.js silently ignores a morph whose
// vertex count disagrees with the geometry it is attached to. The result is a
// wireframe frozen at the undeformed shape while the surfaces it outlines move
// away from it — black lines left behind in mid-air.
//
// So the overlay gets its own morph in SOURCE numbering. Every path that
// installs a displacement goes through here for its wireframe children.

/**
 * Give a named LineSegments child its own morph, in source vertex numbering.
 *
 * `mesh` drives the influence: the child mirrors `mesh.morphTargetInfluences[0]`
 * on every render, so the slider moves both without a second subscription.
 *
 * A no-op when the child is absent or its vertex count disagrees with
 * `sourceDisplacement` — a child whose positions came from somewhere else is not
 * ours to drive, and installing a mismatched morph would render as nothing at all.
 */
export function setSourceMorph(
    mesh: THREE.Mesh,
    childName: string,
    sourceDisplacement: Float32Array,
): void {
    const child = mesh.getObjectByName(childName);
    if (!(child instanceof THREE.LineSegments)) return;
    const geom = child.geometry as THREE.BufferGeometry;
    const position = geom.getAttribute("position");
    if (!position || position.count * 3 !== sourceDisplacement.length) return;

    geom.morphAttributes.position = [new THREE.BufferAttribute(sourceDisplacement, 3)];
    geom.morphTargetsRelative = true;
    child.updateMorphTargets();
    const mat = child.material as THREE.Material;
    if (mat && "morphTargets" in mat) {
        (mat as unknown as {morphTargets: boolean}).morphTargets = true;
        mat.needsUpdate = true;
    }
    child.onBeforeRender = () => {
        const influence = mesh.morphTargetInfluences?.[0] ?? 0;
        if (child.morphTargetInfluences) child.morphTargetInfluences[0] = influence;
    };
    // The shared position buffer was re-uploaded when the parent's geometry was
    // rebuilt; drop this geometry's VAO so it binds the current one.
    geom.dispatchEvent({type: "dispose"});
}
