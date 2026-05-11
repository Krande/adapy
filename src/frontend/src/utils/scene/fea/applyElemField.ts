// Apply an element field (AFEL) to the mesh as per-element vertex
// colours. Element fields don't have a per-node value — they live on
// integration points inside each element — so the colour at every
// vertex of an element is the same scalar after our reduction stack
// collapses the (n_ips, n_components) block to a single number.
//
// Pipeline per element:
//   1. Layer filter           — pick the IP rows matching ``layer``
//                                ("top"/"bottom"/"mid"/"all") out of
//                                the bucket's ``ip_layout``. Solid
//                                elements with no layer metadata fall
//                                through to "all".
//   2. IP reduction           — collapse the filtered IPs to one
//                                value per component. Choices follow
//                                the bake's default_view.ip_reduction.
//   3. Component reduction    — "magnitude" (Euclidean norm of the
//                                first 3 components — same as the
//                                nodal path) or pick a single
//                                component out of ``field.components``.
//   4. Colormap sample        — normalise against the field's
//                                rolled-up scalar_range and sample
//                                the active colormap.
//   5. Vertex write           — every vertex inside the element's
//                                AFEM draw range gets that RGB.
//
// Vertices not covered by any element (e.g. orphan nodes on
// line-only meshes, or future cases where AFEM doesn't cover a
// vertex) keep the seed grey so the mesh doesn't show black holes.
//
// Warp is decoupled: callers pass an optional ``warpField`` /
// ``warpStepValues`` exactly like ``applyFieldToMesh``. The warp
// source is always nodal in current solvers (displacement field is
// nodal), so the warp path is identical to the AFBL one.

import * as THREE from "three";

import type {FeaManifestField, FeaManifestFieldPerType, FeaScalarRange} from "@/services/viewerApi";
import {getColormap} from "./colormaps";

export interface ApplyElemFieldArgs {
    mesh: THREE.Mesh;
    basePositions: Float32Array;
    /** The element field driving the colour. Must have ``per_type``
     *  populated; the AFBL path runs through ``applyFieldToMesh``. */
    colorField: FeaManifestField;
    /** One ``(n_elements * n_ips * n_components)`` Float32 view per
     *  per_type bucket, in the same order as ``colorField.per_type``.
     *  Caller is responsible for fetching/parsing the AFEL blobs and
     *  picking the right step index out of ``parsed.steps``. */
    perTypeStepValues: Float32Array[];
    /** Layer filter — "top" | "bottom" | "mid" | "all". Buckets with
     *  empty ``ip_layout`` fall through to "all" regardless of this
     *  value (no metadata to filter on). */
    layer: string;
    /** IP reduction — "max_abs" | "mean" | "max" | "min". */
    ipReduction: string;
    /** Component reduction — "magnitude" (vector norm of the first 3
     *  components) or one of ``colorField.components``. */
    reduction: string;
    /** Optional warp source (typically the manifest's displacement
     *  field) — same semantics as ``applyFieldToMesh``. */
    warpField?: FeaManifestField;
    warpStepValues?: Float32Array;
    displacementScale?: number;
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

/** Indices of IPs that match the chosen layer. Empty ``ip_layout``
 *  (single-IP elements, or readers that didn't infer it) returns the
 *  full IP range so the bucket still gets coloured. */
function layerIpIndices(
    bucket: FeaManifestFieldPerType,
    layer: string,
): number[] {
    if (!bucket.ip_layout || bucket.ip_layout.length === 0 || layer === "all") {
        const out: number[] = new Array(bucket.n_ips);
        for (let i = 0; i < bucket.n_ips; i++) out[i] = i;
        return out;
    }
    const out: number[] = [];
    for (let i = 0; i < bucket.ip_layout.length; i++) {
        if (bucket.ip_layout[i].layer === layer) out.push(i);
    }
    // If the requested layer doesn't exist on this bucket (e.g. user
    // picked "top" but the bucket is a solid with no layers), fall
    // back to all IPs rather than zeroing the element. Better to show
    // *something* than to silently grey it out.
    if (out.length === 0) {
        for (let i = 0; i < bucket.n_ips; i++) out.push(i);
    }
    return out;
}

/** Reduce IP values for one (element, component) slot down to a
 *  scalar according to ``ipReduction``. Pulls each IP value directly
 *  out of the bucket's flat step view to avoid per-element
 *  allocation. */
function reduceIps(
    stepView: Float32Array,
    elementBase: number,
    ipIndices: number[],
    n_components: number,
    componentIdx: number,
    mode: string,
): number {
    let acc: number;
    switch (mode) {
        case "max_abs": {
            let best = 0;
            for (let k = 0; k < ipIndices.length; k++) {
                const v = stepView[elementBase + ipIndices[k] * n_components + componentIdx];
                const av = Math.abs(v);
                if (av > Math.abs(best)) best = v;
            }
            acc = best;
            break;
        }
        case "max": {
            let best = -Infinity;
            for (let k = 0; k < ipIndices.length; k++) {
                const v = stepView[elementBase + ipIndices[k] * n_components + componentIdx];
                if (v > best) best = v;
            }
            acc = isFinite(best) ? best : 0;
            break;
        }
        case "min": {
            let best = Infinity;
            for (let k = 0; k < ipIndices.length; k++) {
                const v = stepView[elementBase + ipIndices[k] * n_components + componentIdx];
                if (v < best) best = v;
            }
            acc = isFinite(best) ? best : 0;
            break;
        }
        default: {
            // "mean" and any unknown mode fall here. Defaulting to
            // mean rather than throwing keeps the picker resilient
            // to manifest-schema drift without a noisy log on every
            // step change.
            let sum = 0;
            let count = 0;
            for (let k = 0; k < ipIndices.length; k++) {
                const v = stepView[elementBase + ipIndices[k] * n_components + componentIdx];
                if (isFinite(v)) {
                    sum += v;
                    count++;
                }
            }
            acc = count > 0 ? sum / count : 0;
            break;
        }
    }
    return acc;
}

/** Same shape as ``applyFieldToMesh`` but takes the AFEL render path
 *  instead. Caller resolves the warp source separately (always nodal
 *  in current solvers) and passes it in. */
export function applyElemFieldToMesh(args: ApplyElemFieldArgs): void {
    const {
        mesh,
        basePositions,
        colorField,
        perTypeStepValues,
        layer,
        ipReduction,
        reduction,
        warpField,
        warpStepValues,
        displacementScale = 1,
        colormap: colormapName,
    } = args;

    if (!colorField.per_type) {
        throw new Error(
            `applyElemFieldToMesh: field ${colorField.name_canonical} has no per_type buckets`,
        );
    }
    if (perTypeStepValues.length !== colorField.per_type.length) {
        throw new Error(
            `applyElemFieldToMesh: ${perTypeStepValues.length} step views for ` +
            `${colorField.per_type.length} buckets`,
        );
    }

    const colormap = getColormap(colormapName);
    const geometry = mesh.geometry;
    const n_points = basePositions.length / 3;
    const n_components = colorField.components.length;

    const isMagnitude = reduction === "magnitude";
    const compIdx = isMagnitude ? -1 : componentIndex(colorField, reduction);

    // Vertex colour seed: medium grey for any vertex not covered by an
    // element draw range. Without this seed, the colour attribute
    // would be zero-initialised and render black where AFEM doesn't
    // reach (typically nowhere on a healthy mesh, but defensive).
    const out_colors = new Float32Array(n_points * 3);
    for (let i = 0; i < out_colors.length; i++) out_colors[i] = 0.5;

    const [rangeMin, rangeMax] = pickRange(colorField, reduction);
    const range = rangeMax - rangeMin;
    const scaleColor = range > 0 ? 1 / range : 0;

    // Pull the AFEM draw ranges off the CustomBatchedMesh so we can
    // map element labels back to vertex spans. ``drawRanges`` keys
    // are ``E${label}`` strings (see load_fea_streaming.installAfemUserData);
    // values are ``[startVertexIdx, countVertexIdx]`` already
    // multiplied by 3 to land in the index buffer's vertex-index units.
    const drawRanges = (mesh as unknown as {
        drawRanges?: Map<string, [number, number]>;
    }).drawRanges;
    if (!drawRanges) {
        throw new Error(
            "applyElemFieldToMesh: mesh has no drawRanges Map; " +
            "AFEM sidecar wiring is required for element-field rendering",
        );
    }
    const indexAttr = geometry.getIndex();
    if (!indexAttr) {
        throw new Error("applyElemFieldToMesh: mesh geometry has no index buffer");
    }
    const indexArr = indexAttr.array as Uint16Array | Uint32Array;

    const tmpRgb = new Float32Array(3);

    // Per-bucket loop. Each bucket is one element type; the AFEM map
    // collapses across types so a single ``drawRanges.get(...)`` works
    // regardless of which bucket the label came from.
    for (let b = 0; b < colorField.per_type.length; b++) {
        const bucket = colorField.per_type[b];
        const stepView = perTypeStepValues[b];
        const expectedLen = bucket.n_elements * bucket.n_ips * n_components;
        if (stepView.length !== expectedLen) {
            throw new Error(
                `applyElemFieldToMesh: bucket ${bucket.elem_type} step view ` +
                `length ${stepView.length} != expected ${expectedLen}`,
            );
        }
        const ipIndices = layerIpIndices(bucket, layer);
        const elemStride = bucket.n_ips * n_components;

        for (let e = 0; e < bucket.n_elements; e++) {
            const elemBase = e * elemStride;

            let scalar: number;
            if (isMagnitude) {
                // Magnitude across the first 3 components, computed
                // *after* per-component IP reduction. Sequence
                // matches the bake's scalar_range_magnitude
                // (||u||-of-reduced-IPs, not reduced-IP-of-||u||) —
                // important so the colour LUT range matches the
                // rendered values.
                const dx = n_components >= 1
                    ? reduceIps(stepView, elemBase, ipIndices, n_components, 0, ipReduction)
                    : 0;
                const dy = n_components >= 2
                    ? reduceIps(stepView, elemBase, ipIndices, n_components, 1, ipReduction)
                    : 0;
                const dz = n_components >= 3
                    ? reduceIps(stepView, elemBase, ipIndices, n_components, 2, ipReduction)
                    : 0;
                scalar = Math.sqrt(dx * dx + dy * dy + dz * dz);
            } else if (compIdx >= 0) {
                scalar = reduceIps(stepView, elemBase, ipIndices, n_components, compIdx, ipReduction);
            } else {
                // Fallback when reduction is neither magnitude nor a
                // known component — keep the element grey instead of
                // crashing. Same shape as the nodal path's silent
                // fallback in applyFieldToMesh.
                scalar = 0;
            }

            const t = isFinite(scalar) ? (scalar - rangeMin) * scaleColor : 0;
            colormap(t, tmpRgb, 0);
            const r = tmpRgb[0], g = tmpRgb[1], bch = tmpRgb[2];

            const label = bucket.element_labels[e];
            const dr = drawRanges.get(`E${label}`);
            if (!dr) continue;
            const [vStart, vCount] = dr;
            for (let i = vStart; i < vStart + vCount; i++) {
                const vIdx = indexArr[i];
                const off = vIdx * 3;
                out_colors[off + 0] = r;
                out_colors[off + 1] = g;
                out_colors[off + 2] = bch;
            }
        }
    }

    // ── Position + morph attribute (warp). Same shape as
    //    applyFieldToMesh: reset to base, install the displacement as
    //    a morph delta, mark the geometry dirty so three.js rebuilds
    //    the morph texture on the next render.
    const posAttr = geometry.getAttribute("position");
    if (posAttr) {
        (posAttr.array as Float32Array).set(basePositions);
        posAttr.needsUpdate = true;
    }

    const displacement = new Float32Array(basePositions.length);
    if (warpField && warpStepValues) {
        const warpComponents = warpField.components.length;
        if (warpStepValues.length !== n_points * warpComponents) {
            throw new Error(
                `applyElemFieldToMesh: warpStepValues length ${warpStepValues.length} ` +
                `doesn't match n_points*warpComponents (${n_points}*${warpComponents}=` +
                `${n_points * warpComponents})`,
            );
        }
        for (let v = 0; v < n_points; v++) {
            const wb = v * warpComponents;
            const pb = v * 3;
            displacement[pb + 0] = warpStepValues[wb] || 0;
            displacement[pb + 1] = warpComponents >= 2 ? warpStepValues[wb + 1] || 0 : 0;
            displacement[pb + 2] = warpComponents >= 3 ? warpStepValues[wb + 2] || 0 : 0;
        }
    }
    geometry.morphAttributes.position = [
        new THREE.BufferAttribute(displacement, 3),
    ];
    geometry.morphTargetsRelative = true;

    // Colour attribute install / replace.
    const existingColor = geometry.getAttribute("color");
    if (existingColor && existingColor.itemSize === 3) {
        (existingColor.array as Float32Array).set(out_colors);
        existingColor.needsUpdate = true;
    } else {
        geometry.setAttribute("color", new THREE.BufferAttribute(out_colors, 3));
    }

    if (!mesh.morphTargetInfluences) {
        mesh.morphTargetInfluences = [displacementScale];
    } else {
        mesh.morphTargetInfluences[0] = displacementScale;
    }
    if (!mesh.morphTargetDictionary) {
        mesh.morphTargetDictionary = {displacement: 0};
    }

    const enableShaderFlags = (mat: THREE.Material) => {
        let dirty = false;
        if ("vertexColors" in mat && (mat as unknown as {vertexColors: unknown}).vertexColors !== true) {
            (mat as unknown as {vertexColors: boolean}).vertexColors = true;
            dirty = true;
        }
        if ("morphTargets" in mat && (mat as unknown as {morphTargets: unknown}).morphTargets !== true) {
            (mat as unknown as {morphTargets: boolean}).morphTargets = true;
            dirty = true;
        }
        if (dirty) mat.needsUpdate = true;
    };
    if (Array.isArray(mesh.material)) {
        mesh.material.forEach(enableShaderFlags);
    } else if (mesh.material) {
        enableShaderFlags(mesh.material as THREE.Material);
    }

    geometry.computeVertexNormals();
    geometry.dispatchEvent({type: "dispose"});
}
