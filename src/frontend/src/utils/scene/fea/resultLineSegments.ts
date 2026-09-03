import * as THREE from "three";

import {selectedSegments} from "./lineSegmentIds";

const RESULT_LINE_SEGMENTS = "__fea_result_line_segments__";
const RESULT_LINE_HIGHLIGHT = "__fea_result_line_highlight__";

/** The selection colour, matched to `selectedMaterial`. */
const HIGHLIGHT_COLOR = 0x0000ff;

export function clearResultLineSegments(mesh: THREE.Mesh): void {
    for (const name of [RESULT_LINE_SEGMENTS, RESULT_LINE_HIGHLIGHT]) {
        const existing = mesh.getObjectByName(name) as THREE.LineSegments | undefined;
        if (!existing) continue;
        existing.removeFromParent();
        existing.geometry.dispose();
        const material = existing.material;
        if (Array.isArray(material)) material.forEach((item) => item.dispose());
        else material.dispose();
    }
    delete mesh.userData.__feaLineSelection;
}

/** Install a result-coloured fallback for line elements without solid faces.
 * Positions, colours and morph deltas contain two duplicated vertices per
 * element, so discontinuous element values never bleed across beam ends. */
export function installResultLineSegments(
    mesh: THREE.Mesh,
    positions: Float32Array,
    colors: Float32Array,
    displacement: Float32Array,
    segmentIds: readonly string[] = [],
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

    // A generic hook rather than an import from the selection machinery: the
    // mesh this hangs off is a CustomBatchedMesh, which has no business knowing
    // about FEA line rendering, and this keeps the dependency pointing one way.
    if (segmentIds.length) {
        mesh.userData.__feaLineSelection = (selected: readonly string[]) =>
            highlightResultLineSegments(mesh, positions, displacement, segmentIds, selected);
    }
}

/**
 * Draw the selected elements' line segments in the selection colour.
 *
 * A separate LineSegments rather than a recolour of the base one: the base
 * geometry's colour attribute is the result field, and overwriting part of it
 * would mean restoring it exactly on deselect.
 */
export function highlightResultLineSegments(
    mesh: THREE.Mesh,
    positions: Float32Array,
    displacement: Float32Array,
    segmentIds: readonly string[],
    selected: readonly string[],
): void {
    const previous = mesh.getObjectByName(RESULT_LINE_HIGHLIGHT) as THREE.LineSegments | undefined;
    if (previous) {
        previous.removeFromParent();
        previous.geometry.dispose();
        (previous.material as THREE.Material).dispose();
    }

    const picked = selectedSegments(segmentIds, selected);
    if (picked.length === 0) return;

    const pos = new Float32Array(picked.length * 6);
    const disp = new Float32Array(picked.length * 6);
    for (let i = 0; i < picked.length; i++) {
        // Two vertices per segment, three floats each.
        const from = picked[i] * 6;
        pos.set(positions.subarray(from, from + 6), i * 6);
        disp.set(displacement.subarray(from, from + 6), i * 6);
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geometry.morphAttributes.position = [new THREE.BufferAttribute(disp, 3)];
    geometry.morphTargetsRelative = true;
    const material = new THREE.LineBasicMaterial({
        color: HIGHLIGHT_COLOR,
        // Depth ON: a highlight that draws through the hull looked, on the deck
        // this was built against, exactly like a stray line across the model.
        // Being hidden behind geometry is the lesser problem.
        depthTest: true,
    });
    const highlight = new THREE.LineSegments(geometry, material);
    highlight.name = RESULT_LINE_HIGHLIGHT;
    // Above the result lines (4) and the element edges (3).
    highlight.renderOrder = 9;
    highlight.frustumCulled = false;
    highlight.layers.set(1);
    highlight.updateMorphTargets();
    highlight.onBeforeRender = () => {
        const influence = mesh.morphTargetInfluences?.[0] ?? 0;
        if (highlight.morphTargetInfluences) highlight.morphTargetInfluences[0] = influence;
    };
    mesh.add(highlight);
}
