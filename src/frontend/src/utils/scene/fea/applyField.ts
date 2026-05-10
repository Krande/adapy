// Apply a single (field, component, step) selection to a baked
// FEA mesh: deforms positions in-place by the displacement values
// (first 3 components of vector fields) and writes per-vertex
// colours from the chosen scalar reduction → viridis LUT.
//
// CPU-side, no custom shader. THREE's MeshStandardMaterial with
// vertexColors handles the rendering. Step changes invoke this
// helper again with a new step's Float32Array; the per-frame cost
// is one full vertex iteration which is fine up to ~1M points.

import * as THREE from "three";

import type {FeaManifestField, FeaScalarRange} from "@/services/viewerApi";
import {viridis} from "./viridis";

export interface ApplyFieldArgs {
    geometry: THREE.BufferGeometry;
    /** Original (un-deformed) vertex positions, length 3*n_points.
     * Cached on the mesh so step scrubs don't accumulate displacement
     * onto already-displaced positions. */
    basePositions: Float32Array;
    /** This step's per-node values, length n_points * n_components. */
    stepValues: Float32Array;
    field: FeaManifestField;
    /** "magnitude" or one of the field's component names. */
    reduction: string;
    /** Multiplier applied to the displacement vector before adding to
     * basePositions. Use 1 for true-scale; bigger values exaggerate
     * deformation for visualisation. */
    displacementScale?: number;
}

function pickRange(field: FeaManifestField, reduction: string): [number, number] {
    const range: FeaScalarRange = field.scalar_range;
    const r = range[reduction];
    if (r) return [r[0], r[1]];
    // Fall back to first component's range or [0, 1].
    if (field.components.length > 0) {
        const fallback = range[field.components[0]];
        if (fallback) return [fallback[0], fallback[1]];
    }
    return [0, 1];
}

function componentIndex(field: FeaManifestField, reduction: string): number {
    return field.components.indexOf(reduction);
}

/** Update geometry's position + colour attributes for the chosen
 * step. Idempotent: always derives positions from basePositions, so
 * repeated calls just replace. */
export function applyFieldToMesh(args: ApplyFieldArgs): void {
    const {
        geometry,
        basePositions,
        stepValues,
        field,
        reduction,
        displacementScale = 1,
    } = args;

    const n_points = basePositions.length / 3;
    const n_components = field.components.length;
    if (stepValues.length !== n_points * n_components) {
        throw new Error(
            `applyFieldToMesh: stepValues length ${stepValues.length} doesn't match ` +
            `n_points*n_components (${n_points}*${n_components}=${n_points * n_components})`,
        );
    }

    const out_positions = new Float32Array(basePositions.length);
    const out_colors = new Float32Array(n_points * 3);

    const [rangeMin, rangeMax] = pickRange(field, reduction);
    const range = rangeMax - rangeMin;
    const scaleColor = range > 0 ? 1 / range : 0;

    const isVector = field.kind.startsWith("vector");
    const isMagnitude = reduction === "magnitude";
    const compIdx = isMagnitude ? -1 : componentIndex(field, reduction);

    for (let v = 0; v < n_points; v++) {
        const base = v * 3;
        const stride = v * n_components;

        // Displacement: first 3 components for vector fields. Scalar
        // fields skip the deformation step.
        let dx = 0;
        let dy = 0;
        let dz = 0;
        if (isVector) {
            dx = stepValues[stride] || 0;
            dy = n_components >= 2 ? stepValues[stride + 1] || 0 : 0;
            dz = n_components >= 3 ? stepValues[stride + 2] || 0 : 0;
        }
        out_positions[base + 0] = basePositions[base + 0] + dx * displacementScale;
        out_positions[base + 1] = basePositions[base + 1] + dy * displacementScale;
        out_positions[base + 2] = basePositions[base + 2] + dz * displacementScale;

        // Scalar value used by the colormap. Magnitude for vectors;
        // direct read for component or scalar fields.
        let scalar: number;
        if (isMagnitude) {
            scalar = Math.sqrt(dx * dx + dy * dy + dz * dz);
        } else if (compIdx >= 0) {
            scalar = stepValues[stride + compIdx] || 0;
        } else {
            // Scalar field, n_components=1.
            scalar = stepValues[stride] || 0;
        }
        const t = isFinite(scalar) ? (scalar - rangeMin) * scaleColor : 0;
        viridis(t, out_colors, base);
    }

    // Replace the buffers; THREE re-uploads on next render frame.
    const posAttr = geometry.getAttribute("position");
    if (posAttr) {
        (posAttr.array as Float32Array).set(out_positions);
        posAttr.needsUpdate = true;
    }

    // Vertex colours land on a fresh attribute since the GLB doesn't
    // carry one. The material's ``vertexColors`` flag is flipped by
    // the streaming load orchestrator before the first apply.
    const existingColor = geometry.getAttribute("color");
    if (existingColor && existingColor.itemSize === 3) {
        (existingColor.array as Float32Array).set(out_colors);
        existingColor.needsUpdate = true;
    } else {
        geometry.setAttribute("color", new THREE.BufferAttribute(out_colors, 3));
    }

    geometry.computeVertexNormals();
}
