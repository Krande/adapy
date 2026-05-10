// Apply a single (field, component, step) selection to a baked
// FEA mesh: installs the displacement as a morph target on the
// geometry so the deformation factor lives in
// ``mesh.morphTargetInfluences[0]`` instead of being baked into
// the position attribute. Vertex colours are still CPU-computed
// per step + reduction since they don't depend on the factor.
//
// The morph-target route is the right primitive here because:
//   * the deformation animation just sweeps the influence uniform —
//     zero CPU per frame regardless of vertex count;
//   * CustomBatchedMesh's selection overlay already syncs with
//     morph deformation via its onBeforeRender hook, so highlighted
//     elements track the deformed shape automatically;
//   * raycasting on morphed geometry is supported by THREE out of
//     the box (CustomBatchedMesh's custom raycast respects it too).

import * as THREE from "three";

import type {FeaManifestField, FeaScalarRange} from "@/services/viewerApi";
import {viridis} from "./viridis";

export interface ApplyFieldArgs {
    /** The mesh whose geometry we deform. We need the mesh (not just
     * the geometry) so we can update ``morphTargetInfluences``. */
    mesh: THREE.Mesh;
    /** Original (un-deformed) vertex positions, length 3*n_points.
     * Cached on the mesh so step scrubs don't accumulate displacement
     * onto already-displaced positions. The geometry's position
     * attribute is reset to this snapshot on every apply, so the
     * morph attribute can hold the un-scaled displacement. */
    basePositions: Float32Array;
    /** This step's per-node values, length n_points * n_components. */
    stepValues: Float32Array;
    field: FeaManifestField;
    /** "magnitude" or one of the field's component names. */
    reduction: string;
    /** Initial deformation factor. Animation drivers update
     * ``mesh.morphTargetInfluences[0]`` directly afterwards; this is
     * just the value the slider was at when the user pressed apply. */
    displacementScale?: number;
}

function pickRange(field: FeaManifestField, reduction: string): [number, number] {
    const range: FeaScalarRange = field.scalar_range;
    const r = range[reduction];
    if (r) return [r[0], r[1]];
    if (field.components.length > 0) {
        const fallback = range[field.components[0]];
        if (fallback) return [fallback[0], fallback[1]];
    }
    return [0, 1];
}

function componentIndex(field: FeaManifestField, reduction: string): number {
    return field.components.indexOf(reduction);
}

/** Update the geometry's morph target + colour attribute for the
 * chosen step, and seed the mesh's morphTargetInfluences[0] with
 * the requested factor. Idempotent: always derives from
 * basePositions and the step's raw values, so repeated calls just
 * replace the morph delta and colours. */
export function applyFieldToMesh(args: ApplyFieldArgs): void {
    const {
        mesh,
        basePositions,
        stepValues,
        field,
        reduction,
        displacementScale = 1,
    } = args;

    const geometry = mesh.geometry;
    const n_points = basePositions.length / 3;
    const n_components = field.components.length;
    if (stepValues.length !== n_points * n_components) {
        throw new Error(
            `applyFieldToMesh: stepValues length ${stepValues.length} doesn't match ` +
            `n_points*n_components (${n_points}*${n_components}=${n_points * n_components})`,
        );
    }

    const isVector = field.kind.startsWith("vector");
    const isMagnitude = reduction === "magnitude";
    const compIdx = isMagnitude ? -1 : componentIndex(field, reduction);

    // Displacement vector per vertex (un-scaled). Lives as the morph
    // delta — THREE adds ``influence * morphAttr`` to the base
    // position when morphTargetsRelative is true.
    const displacement = new Float32Array(basePositions.length);
    const out_colors = new Float32Array(n_points * 3);

    const [rangeMin, rangeMax] = pickRange(field, reduction);
    const range = rangeMax - rangeMin;
    const scaleColor = range > 0 ? 1 / range : 0;

    for (let v = 0; v < n_points; v++) {
        const base = v * 3;
        const stride = v * n_components;

        let dx = 0;
        let dy = 0;
        let dz = 0;
        if (isVector) {
            dx = stepValues[stride] || 0;
            dy = n_components >= 2 ? stepValues[stride + 1] || 0 : 0;
            dz = n_components >= 3 ? stepValues[stride + 2] || 0 : 0;
        }
        displacement[base + 0] = dx;
        displacement[base + 1] = dy;
        displacement[base + 2] = dz;

        let scalar: number;
        if (isMagnitude) {
            scalar = Math.sqrt(dx * dx + dy * dy + dz * dz);
        } else if (compIdx >= 0) {
            scalar = stepValues[stride + compIdx] || 0;
        } else {
            scalar = stepValues[stride] || 0;
        }
        const t = isFinite(scalar) ? (scalar - rangeMin) * scaleColor : 0;
        viridis(t, out_colors, base);
    }

    // 1. Reset the position attribute to the un-deformed base. The
    //    morph delta is what carries the deformation; the base must
    //    stay static or repeated applies stack onto each other.
    const posAttr = geometry.getAttribute("position");
    if (posAttr) {
        (posAttr.array as Float32Array).set(basePositions);
        posAttr.needsUpdate = true;
    }

    // 2. Install / update the displacement morph attribute. Reuse
    //    the underlying buffer when possible so step scrubs avoid
    //    re-allocating one Float32Array per vertex per frame.
    if (!geometry.morphAttributes.position) {
        geometry.morphAttributes.position = [];
    }
    const existingMorph = geometry.morphAttributes.position[0];
    if (
        existingMorph &&
        (existingMorph.array as Float32Array).length === displacement.length
    ) {
        (existingMorph.array as Float32Array).set(displacement);
        existingMorph.needsUpdate = true;
    } else {
        geometry.morphAttributes.position[0] = new THREE.BufferAttribute(displacement, 3);
    }
    // Additive morphing: position = base + influence * morphAttr.
    // Without this THREE blends with (1 - influence) on the base.
    geometry.morphTargetsRelative = true;

    // 3. Vertex colours — independent of the morph factor.
    const existingColor = geometry.getAttribute("color");
    if (existingColor && existingColor.itemSize === 3) {
        (existingColor.array as Float32Array).set(out_colors);
        existingColor.needsUpdate = true;
    } else {
        geometry.setAttribute("color", new THREE.BufferAttribute(out_colors, 3));
    }

    // 4. Seed the influence. Animation drivers (RAF) update this
    //    directly afterwards.
    if (!mesh.morphTargetInfluences) {
        mesh.morphTargetInfluences = [displacementScale];
    } else {
        mesh.morphTargetInfluences[0] = displacementScale;
    }
    if (!mesh.morphTargetDictionary) {
        mesh.morphTargetDictionary = {displacement: 0};
    }

    // 5. Material flags. vertexColors flipped on by the orchestrator;
    //    morphTargets must be on for the renderer to consume the
    //    morph attribute.
    const enableMorphFlag = (mat: THREE.Material) => {
        if ("morphTargets" in mat) {
            (mat as any).morphTargets = true;
            mat.needsUpdate = true;
        }
    };
    if (Array.isArray(mesh.material)) {
        mesh.material.forEach(enableMorphFlag);
    } else if (mesh.material) {
        enableMorphFlag(mesh.material as THREE.Material);
    }

    // Normals depend on the deformed shape; recompute against the
    // base positions only — the morph delta blends per-vertex on the
    // GPU and we don't have a cheap way to reflect that in the CPU
    // normals array. For viz that's acceptable: lighting on the
    // morphed shape uses base normals, which is consistent with how
    // GLTF morph clips behave by default.
    geometry.computeVertexNormals();
}
