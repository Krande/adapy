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
import {getColormap} from "./colormaps";

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
    /** Field driving the per-vertex colour. */
    colorField: FeaManifestField;
    /** Step values for ``colorField``, length ``n_points *
     * colorField.components.length``. */
    colorStepValues: Float32Array;
    /** "magnitude" or one of ``colorField.components``. */
    reduction: string;
    /** Field driving the morph delta. Omit to leave the geometry
     * undeformed (zero-magnitude morph delta installed so the influence
     * channel still exists for downstream consumers like the line
     * wireframe — they expect morphTargetInfluences[0] to be a number).
     *
     * Decoupling colour and warp lets the picker show stress / strain
     * fields on the *deformed* shape: the user selects the field they
     * want to inspect, the simulation panel chooses the displacement
     * field as the warp source. Reaction fields explicitly pass no
     * warp source so the geometry stays static. */
    warpField?: FeaManifestField;
    /** Step values for ``warpField``, length ``n_points *
     * warpField.components.length``. Only the first 3 components are
     * read (DX, DY, DZ); rotational DOFs and other slots beyond 3 are
     * ignored for warp purposes. */
    warpStepValues?: Float32Array;
    /** Initial deformation factor. Animation drivers update
     * ``mesh.morphTargetInfluences[0]`` directly afterwards; this is
     * just the value the slider was at when the user pressed apply. */
    displacementScale?: number;
    /** Colormap ID — one of the keys in
     * ``utils/scene/fea/colormaps.COLORMAPS``. Falls back to viridis
     * when missing/unknown so a typo in state doesn't render the mesh
     * black. */
    colormap?: string;
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
        colorField,
        colorStepValues,
        reduction,
        warpField,
        warpStepValues,
        displacementScale = 1,
        colormap: colormapName,
    } = args;
    const colormap = getColormap(colormapName);

    const geometry = mesh.geometry;
    const n_points = basePositions.length / 3;
    const n_components = colorField.components.length;
    if (colorStepValues.length !== n_points * n_components) {
        throw new Error(
            `applyFieldToMesh: colorStepValues length ${colorStepValues.length} doesn't match ` +
            `n_points*n_components (${n_points}*${n_components}=${n_points * n_components})`,
        );
    }

    // Warp source — independent of the colour source. ``warpField`` is
    // typically the manifest's displacement field; ``colorField`` is
    // whatever the user picked to visualise. When both are the same
    // (the user picked displacement), this still works — the loop
    // reads two component slices from the same backing array.
    let warpComponents = 0;
    if (warpField && warpStepValues) {
        warpComponents = warpField.components.length;
        if (warpStepValues.length !== n_points * warpComponents) {
            throw new Error(
                `applyFieldToMesh: warpStepValues length ${warpStepValues.length} doesn't match ` +
                `n_points*warpComponents (${n_points}*${warpComponents}=${n_points * warpComponents})`,
            );
        }
    }

    const isMagnitude = reduction === "magnitude";
    const compIdx = isMagnitude ? -1 : componentIndex(colorField, reduction);

    // Displacement vector per vertex (un-scaled). Lives as the morph
    // delta — THREE adds ``influence * morphAttr`` to the base
    // position when morphTargetsRelative is true. Always full-length
    // even when no warp source is active; the influence stays at
    // whatever the user has on the slider but the deltas are zero,
    // so the geometry sits at base positions.
    const displacement = new Float32Array(basePositions.length);
    const out_colors = new Float32Array(n_points * 3);

    const [rangeMin, rangeMax] = pickRange(colorField, reduction);
    const range = rangeMax - rangeMin;
    const scaleColor = range > 0 ? 1 / range : 0;

    for (let v = 0; v < n_points; v++) {
        const base = v * 3;
        const colorStride = v * n_components;

        if (warpComponents > 0 && warpStepValues) {
            const warpStride = v * warpComponents;
            displacement[base + 0] = warpStepValues[warpStride] || 0;
            displacement[base + 1] = warpComponents >= 2 ? warpStepValues[warpStride + 1] || 0 : 0;
            displacement[base + 2] = warpComponents >= 3 ? warpStepValues[warpStride + 2] || 0 : 0;
        }
        // else: displacement[base..base+2] left at 0 from Float32Array
        //       initialisation, so morph delta is identity.

        let scalar: number;
        if (isMagnitude) {
            // Magnitude of the *colour* field's first 3 components.
            // For a vector3 displacement this is ||u||; for a
            // displacement-warped scalar field it'd reduce to the
            // value itself (n_components == 1).
            const dx0 = colorStepValues[colorStride] || 0;
            const dy0 = n_components >= 2 ? colorStepValues[colorStride + 1] || 0 : 0;
            const dz0 = n_components >= 3 ? colorStepValues[colorStride + 2] || 0 : 0;
            scalar = Math.sqrt(dx0 * dx0 + dy0 * dy0 + dz0 * dz0);
        } else if (compIdx >= 0) {
            scalar = colorStepValues[colorStride + compIdx] || 0;
        } else {
            scalar = colorStepValues[colorStride] || 0;
        }
        const t = isFinite(scalar) ? (scalar - rangeMin) * scaleColor : 0;
        colormap(t, out_colors, base);
    }

    // 1. Reset the position attribute to the un-deformed base. The
    //    morph delta is what carries the deformation; the base must
    //    stay static or repeated applies stack onto each other.
    const posAttr = geometry.getAttribute("position");
    if (posAttr) {
        (posAttr.array as Float32Array).set(basePositions);
        posAttr.needsUpdate = true;
    }

    // 2. Install the displacement morph attribute. Always replace with
    //    a fresh BufferAttribute — three.js's WebGLMorphtargets bakes
    //    morph data into a DataArrayTexture once at allocation and
    //    only rebuilds the texture when the morph target *count*
    //    changes (see WebGLMorphtargets.js: `entry.count !==
    //    morphTargetsCount`). Mutating the existing BufferAttribute's
    //    array in place is invisible to the GPU. So on step changes
    //    we additionally fire the geometry's "dispose" event below to
    //    invalidate the cached morph texture entry; combined with
    //    fresh BufferAttribute references that triggers a full
    //    texture rebuild on the next render. Cost on a ~50k-vert FEA
    //    mesh is one re-upload of position/index/color attributes —
    //    negligible at human-scale step-change rates.
    geometry.morphAttributes.position = [
        new THREE.BufferAttribute(displacement, 3),
    ];
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

    // 5. Material flags. CustomBatchedMesh holds material as an Array
    //    of three slots (original / selected-overlay / invisible);
    //    every slot that ends up rendering vertices needs
    //    `vertexColors` so the per-vertex colour attribute we just
    //    installed is actually used by the shader, and `morphTargets`
    //    so the morph attribute drives geometry. Setting these on the
    //    array as a whole (a previous bug) is silent — the
    //    "vertexColors" in (Array) check is false, so the flag never
    //    landed and the colormap never appeared on the deformed mesh.
    const enableShaderFlags = (mat: THREE.Material) => {
        let dirty = false;
        if ("vertexColors" in mat && (mat as any).vertexColors !== true) {
            (mat as any).vertexColors = true;
            dirty = true;
        }
        if ("morphTargets" in mat && (mat as any).morphTargets !== true) {
            (mat as any).morphTargets = true;
            dirty = true;
        }
        if (dirty) mat.needsUpdate = true;
    };
    if (Array.isArray(mesh.material)) {
        mesh.material.forEach(enableShaderFlags);
    } else if (mesh.material) {
        enableShaderFlags(mesh.material as THREE.Material);
    }

    // Normals depend on the deformed shape; recompute against the
    // base positions only — the morph delta blends per-vertex on the
    // GPU and we don't have a cheap way to reflect that in the CPU
    // normals array. For viz that's acceptable: lighting on the
    // morphed shape uses base normals, which is consistent with how
    // GLTF morph clips behave by default.
    geometry.computeVertexNormals();

    // 6. Force the renderer to rebuild the cached morph DataArrayTexture.
    //    See the long-form comment on the morphAttributes assignment
    //    above: the texture is keyed by geometry+count, and step
    //    changes don't change the count, so we drop the cache by
    //    dispatching the geometry's 'dispose' event. Three.js's
    //    WebGLMorphtargets and WebGLGeometries listen for this event
    //    to clear morph textures + GPU attribute bindings; the next
    //    render rebuilds them from the fresh BufferAttribute we just
    //    installed.
    geometry.dispatchEvent({type: "dispose"});
}
