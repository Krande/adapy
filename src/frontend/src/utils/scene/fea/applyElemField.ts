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
import {
    ensureElementLocalVertices,
    expandSourceTriples,
    sourceVertexIndices,
} from "./elementLocalGeometry";
import {clearResultPointMarkers, installResultPointMarkers} from "./resultPointMarkers";
import {clearResultLineSegments, installResultLineSegments} from "./resultLineSegments";
import {segmentRangeIds} from "./lineSegmentIds";
import {setSourceMorph} from "./sourceMorph";
import {translationOffsets, warpValue} from "./warpComponents";

type IpLayoutEntry = FeaManifestFieldPerType["ip_layout"][number];
type SourceWeight = [sourceVertex: number, weight: number];

function resultPointSourceWeights(
    layout: IpLayoutEntry | undefined,
    sourceCorners: number[],
    line: boolean,
): SourceWeight[] {
    if (layout?.node_index !== undefined && sourceCorners[layout.node_index] !== undefined) {
        return [[sourceCorners[layout.node_index], 1]];
    }
    const natural = layout?.natural_coordinates;
    if (line && natural?.length === 1 && sourceCorners.length >= 2) {
        const t = Math.max(0, Math.min(1, natural[0]));
        return [[sourceCorners[0], 1 - t], [sourceCorners[1], t]];
    }
    if (sourceCorners.length === 0) return [];
    const weight = 1 / sourceCorners.length;
    return sourceCorners.map((source) => [source, weight]);
}

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
    /**
     * Draw the coloured two-vertex fallback for line elements.
     *
     * Only for a deck that CANNOT show beam solids. Where solids exist the beam
     * carries its result on its own surface, and drawing a coloured line as well
     * puts two renderings of the same beam in the same place: the black
     * element-edge overlay fights the coloured line, and neither is legible.
     */
    lineFallback?: boolean;
    /** Smooth-shade by averaging each vertex's element scalars across
     *  the elements that touch it. ``false`` (default) paints the same
     *  colour onto every vertex of an element (piecewise-constant). */
    nodalAverage?: boolean;
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
export function layerIpIndices(
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
export function reduceIps(
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
            let found = false;
            for (let k = 0; k < ipIndices.length; k++) {
                const v = stepView[elementBase + ipIndices[k] * n_components + componentIdx];
                if (!isFinite(v)) continue;
                const av = Math.abs(v);
                if (!found || av > Math.abs(best)) best = v;
                found = true;
            }
            acc = found ? best : NaN;
            break;
        }
        case "max": {
            let best = -Infinity;
            for (let k = 0; k < ipIndices.length; k++) {
                const v = stepView[elementBase + ipIndices[k] * n_components + componentIdx];
                if (v > best) best = v;
            }
            acc = isFinite(best) ? best : NaN;
            break;
        }
        case "min": {
            let best = Infinity;
            for (let k = 0; k < ipIndices.length; k++) {
                const v = stepView[elementBase + ipIndices[k] * n_components + componentIdx];
                if (v < best) best = v;
            }
            acc = isFinite(best) ? best : NaN;
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
            acc = count > 0 ? sum / count : NaN;
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
        nodalAverage = false,
        lineFallback = true,
    } = args;

    clearResultPointMarkers(mesh);
    clearResultLineSegments(mesh);
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
    const renderToSource = nodalAverage
        ? sourceVertexIndices(geometry, n_points)
        : ensureElementLocalVertices(geometry);
    const renderBasePositions = expandSourceTriples(basePositions, renderToSource);
    const n_render_points = renderToSource.length;
    const n_components = colorField.components.length;

    const isMagnitude = reduction === "magnitude";
    const compIdx = isMagnitude ? -1 : componentIndex(colorField, reduction);

    // Vertex colour seed: medium grey for any vertex not covered by an
    // element draw range. Without this seed, the colour attribute
    // would be zero-initialised and render black where AFEM doesn't
    // reach (typically nowhere on a healthy mesh, but defensive).
    const out_colors = new Float32Array(n_render_points * 3);
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

    // Compute one scalar per element from the (n_ips × n_components)
    // block. Same logic for both flat and smooth render paths — only
    // the downstream "where does the scalar land" step differs.
    const computeElementScalar = (
        stepView: Float32Array,
        elemBase: number,
        ipIndices: number[],
    ): number => {
        if (isMagnitude) {
            // Magnitude across the first 3 components, computed
            // *after* per-component IP reduction. Sequence matches
            // the bake's scalar_range_magnitude (||u||-of-reduced-IPs,
            // not reduced-IP-of-||u||) — important so the colour LUT
            // range matches the rendered values.
            const dx = n_components >= 1
                ? reduceIps(stepView, elemBase, ipIndices, n_components, 0, ipReduction)
                : 0;
            const dy = n_components >= 2
                ? reduceIps(stepView, elemBase, ipIndices, n_components, 1, ipReduction)
                : 0;
            const dz = n_components >= 3
                ? reduceIps(stepView, elemBase, ipIndices, n_components, 2, ipReduction)
                : 0;
            return isFinite(dx) && isFinite(dy) && isFinite(dz)
                ? Math.sqrt(dx * dx + dy * dy + dz * dz)
                : NaN;
        }
        if (compIdx >= 0) {
            return reduceIps(stepView, elemBase, ipIndices, n_components, compIdx, ipReduction);
        }
        // Fallback when reduction is neither magnitude nor a known
        // component — keep the element grey instead of crashing.
        // Same shape as the nodal path's silent fallback.
        return 0;
    };

    // Smooth path: accumulate per-vertex sum + count, then colormap
    // once per vertex at the end. Reused Set dedupes each element's
    // vertices so a quad's 4 unique verts don't get the same scalar
    // counted 6 times (6 = 2 triangles × 3 slots). Allocated once
    // outside the per-bucket loop to keep GC noise down.
    const sumValues = nodalAverage ? new Float32Array(n_points) : null;
    const countValues = nodalAverage ? new Uint32Array(n_points) : null;
    const elemVertSet = nodalAverage ? new Set<number>() : null;
    const directVertsBySource = new Map<number, number[]>();
    const markerPositions: number[] = [];
    const markerColors: number[] = [];
    const markerSourceWeights: SourceWeight[][] = [];
    const linePositions: number[] = [];
    const lineColors: number[] = [];
    const lineSourceIndices: number[] = [];
    // One label per VERTEX, pushed beside the vertex itself. See lineSegmentIds:
    // a parallel per-SEGMENT array maintained by a condition is what drifted.
    const lineVertexLabels: number[] = [];

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
            const label = bucket.element_labels[e];
            const dr = drawRanges.get(`E${label}`);

            // Line elements have AFEM entries with zero triangles, so their
            // values cannot colour the face mesh. Draw a two-vertex segment
            // from the manifest connectivity as the no-solid fallback. For
            // Xtract Elements fields the two result slots map to the beam
            // ends; element-average fields use one colour at both ends.
            if (
                lineFallback
                && !dr
                && bucket.elem_type.startsWith("line")
                && colorField.support !== "line_result_point"
                && colorField.support !== "result_point"
            ) {
                const sourceCorners = bucket.element_node_indices?.[e] ?? [];
                if (sourceCorners.length >= 2) {
                    const endpoints = [sourceCorners[0], sourceCorners[sourceCorners.length - 1]];
                    const endpointIps = colorField.support === "element_nodal" && ipIndices.length >= 2
                        ? [ipIndices[0], ipIndices[ipIndices.length - 1]]
                        : [null, null];
                    const averaged = endpointIps[0] === null
                        ? computeElementScalar(stepView, elemBase, ipIndices)
                        : NaN;
                    for (let endpoint = 0; endpoint < 2; endpoint++) {
                        const sourceIdx = endpoints[endpoint];
                        const offset = sourceIdx * 3;
                        const scalar = endpointIps[endpoint] === null
                            ? averaged
                            : computeElementScalar(
                                stepView,
                                elemBase,
                                [endpointIps[endpoint] as number],
                            );
                        if (!isFinite(scalar)) continue;
                        linePositions.push(
                            basePositions[offset],
                            basePositions[offset + 1],
                            basePositions[offset + 2],
                        );
                        const t = (scalar - rangeMin) * scaleColor;
                        colormap(t, tmpRgb, 0);
                        lineColors.push(tmpRgb[0], tmpRgb[1], tmpRgb[2]);
                        lineSourceIndices.push(sourceIdx);
                        lineVertexLabels.push(label);
                    }
                    // A LineSegments pair must be complete. Drop a lone end
                    // when the other endpoint carried an inapplicable NaN.
                    if (lineSourceIndices.length % 2) {
                        lineSourceIndices.splice(-1, 1);
                        linePositions.splice(-3, 3);
                        lineColors.splice(-3, 3);
                        lineVertexLabels.splice(-1, 1);
                    }
                }
            }

            if (
                !mesh.userData.feaBeamSolids
                && (
                    colorField.support === "result_point"
                    || colorField.support === "line_result_point"
                )
            ) {
                const sourceCorners = bucket.element_node_indices?.[e]?.slice() ?? [];
                if (sourceCorners.length === 0 && dr) {
                    const seenSources = new Set<number>();
                    const [vStart, vCount] = dr;
                    for (let i = vStart; i < vStart + vCount; i++) {
                        const sourceIdx = renderToSource[indexArr[i]];
                        if (seenSources.has(sourceIdx)) continue;
                        seenSources.add(sourceIdx);
                        sourceCorners.push(sourceIdx);
                    }
                }
                for (const ip of ipIndices) {
                    const weights = resultPointSourceWeights(
                        bucket.ip_layout?.[ip],
                        sourceCorners,
                        colorField.support === "line_result_point",
                    );
                    if (weights.length === 0) continue;
                    const point = [0, 0, 0];
                    for (const [sourceIdx, weight] of weights) {
                        const offset = sourceIdx * 3;
                        point[0] += basePositions[offset] * weight;
                        point[1] += basePositions[offset + 1] * weight;
                        point[2] += basePositions[offset + 2] * weight;
                    }
                    const scalar = computeElementScalar(stepView, elemBase, [ip]);
                    if (!isFinite(scalar)) continue;
                    const t = (scalar - rangeMin) * scaleColor;
                    colormap(t, tmpRgb, 0);
                    markerPositions.push(point[0], point[1], point[2]);
                    markerColors.push(tmpRgb[0], tmpRgb[1], tmpRgb[2]);
                    markerSourceWeights.push(weights);
                }
            }

            if (!dr) continue;
            const [vStart, vCount] = dr;

            // Xtract Elements fields carry one value per element-local corner.
            // Preserve that variation instead of collapsing all corners through
            // the generic IP reducer. The first-seen vertex order of the AFEM
            // triangle fan follows source connectivity for TRI/QUAD cells.
            if (colorField.support === "element_nodal") {
                directVertsBySource.clear();
                for (let i = vStart; i < vStart + vCount; i++) {
                    const vIdx = indexArr[i];
                    const sourceIdx = renderToSource[vIdx];
                    const occurrences = directVertsBySource.get(sourceIdx);
                    if (occurrences) occurrences.push(vIdx);
                    else directVertsBySource.set(sourceIdx, [vIdx]);
                }
                if (directVertsBySource.size === ipIndices.length) {
                    const cornerOccurrences = Array.from(directVertsBySource.values());
                    for (let corner = 0; corner < ipIndices.length; corner++) {
                        const scalar = computeElementScalar(
                            stepView,
                            elemBase,
                            [ipIndices[corner]],
                        );
                        if (!isFinite(scalar)) continue;
                        if (nodalAverage) {
                            const sourceIdx = renderToSource[cornerOccurrences[corner][0]];
                            sumValues![sourceIdx] += scalar;
                            countValues![sourceIdx] += 1;
                        } else {
                            const t = (scalar - rangeMin) * scaleColor;
                            colormap(t, tmpRgb, 0);
                            for (const vIdx of cornerOccurrences[corner]) {
                                const off = vIdx * 3;
                                out_colors[off + 0] = tmpRgb[0];
                                out_colors[off + 1] = tmpRgb[1];
                                out_colors[off + 2] = tmpRgb[2];
                            }
                        }
                    }
                    continue;
                }
            }

            const scalar = computeElementScalar(stepView, elemBase, ipIndices);

            if (nodalAverage && isFinite(scalar)) {
                // Accumulate the same scalar once per unique vertex
                // of this element. Dedupe via the reused Set: clear,
                // walk, contribute. Each draw range is in vertex-index
                // units (3 entries per triangle), so the same vertex
                // appears multiple times for shared edges within an
                // element's triangle fan.
                elemVertSet!.clear();
                for (let i = vStart; i < vStart + vCount; i++) {
                    const vIdx = indexArr[i];
                    const sourceIdx = renderToSource[vIdx];
                    if (elemVertSet!.has(sourceIdx)) continue;
                    elemVertSet!.add(sourceIdx);
                    sumValues![sourceIdx] += scalar;
                    countValues![sourceIdx] += 1;
                }
            } else if (!nodalAverage) {
                // NaN means the attribute is inapplicable to this element type.
                // Leave the neutral grey seed; never paint it as range minimum.
                if (!isFinite(scalar)) continue;
                const t = (scalar - rangeMin) * scaleColor;
                colormap(t, tmpRgb, 0);
                const r = tmpRgb[0], g = tmpRgb[1], bch = tmpRgb[2];
                for (let i = vStart; i < vStart + vCount; i++) {
                    const vIdx = indexArr[i];
                    const off = vIdx * 3;
                    out_colors[off + 0] = r;
                    out_colors[off + 1] = g;
                    out_colors[off + 2] = bch;
                }
            }
        }
    }

    if (nodalAverage) {
        // Vertices with count===0 stay at the grey seed (no element
        // touched them — line-only verts or AFEM gaps). For
        // touched vertices, divide and colormap.
        for (let v = 0; v < n_render_points; v++) {
            const sourceIdx = renderToSource[v];
            const c = countValues![sourceIdx];
            if (c === 0) continue;
            const avg = sumValues![sourceIdx] / c;
            const t = isFinite(avg) ? (avg - rangeMin) * scaleColor : 0;
            colormap(t, tmpRgb, 0);
            const off = v * 3;
            out_colors[off + 0] = tmpRgb[0];
            out_colors[off + 1] = tmpRgb[1];
            out_colors[off + 2] = tmpRgb[2];
        }
    }

    // ── Position + morph attribute (warp). Same shape as
    //    applyFieldToMesh: reset to base, install the displacement as
    //    a morph delta, mark the geometry dirty so three.js rebuilds
    //    the morph texture on the next render.
    const posAttr = geometry.getAttribute("position");
    if (posAttr) {
        (posAttr.array as Float32Array).set(renderBasePositions);
        posAttr.needsUpdate = true;
    }

    const sourceDisplacement = new Float32Array(basePositions.length);
    if (warpField && warpStepValues) {
        const warpComponents = warpField.components.length;
        if (warpStepValues.length !== n_points * warpComponents) {
            throw new Error(
                `applyElemFieldToMesh: warpStepValues length ${warpStepValues.length} ` +
                `doesn't match n_points*warpComponents (${n_points}*${warpComponents}=` +
                `${n_points * warpComponents})`,
            );
        }
        // Not slots 0..2: a Sesam displacement field leads with `ALL`, a
        // reduction, not an axis. See translationOffsets.
        const axes = translationOffsets(warpField);
        for (let v = 0; v < n_points; v++) {
            const wb = v * warpComponents;
            const pb = v * 3;
            sourceDisplacement[pb + 0] = warpValue(warpStepValues, wb, axes[0]);
            sourceDisplacement[pb + 1] = warpValue(warpStepValues, wb, axes[1]);
            sourceDisplacement[pb + 2] = warpValue(warpStepValues, wb, axes[2]);
        }
    }
    // The element-edge wireframe keeps SOURCE numbering: its index buffer was built
    // from the bake's edge sidecar against the original vertices, and it still holds
    // the original position attribute. The parent mesh does not -- an element field
    // expands the geometry to element-local vertices and swaps the buffer -- so the
    // parent's morph is the wrong length for it, and handing it over left the outline
    // sitting undeformed while the faces moved. Nodal fields never expand, which is
    // why this only ever showed up on an element field.
    setSourceMorph(mesh, "fea-element-edges", sourceDisplacement);

    const displacement = expandSourceTriples(sourceDisplacement, renderToSource);
    const markerDisplacement = new Float32Array(markerSourceWeights.length * 3);
    for (let point = 0; point < markerSourceWeights.length; point++) {
        for (const [sourceIdx, weight] of markerSourceWeights[point]) {
            const sourceOffset = sourceIdx * 3;
            const pointOffset = point * 3;
            markerDisplacement[pointOffset] += sourceDisplacement[sourceOffset] * weight;
            markerDisplacement[pointOffset + 1] += sourceDisplacement[sourceOffset + 1] * weight;
            markerDisplacement[pointOffset + 2] += sourceDisplacement[sourceOffset + 2] * weight;
        }
    }
    installResultPointMarkers(
        mesh,
        new Float32Array(markerPositions),
        new Float32Array(markerColors),
        markerDisplacement,
    );
    const lineDisplacement = new Float32Array(lineSourceIndices.length * 3);
    for (let vertex = 0; vertex < lineSourceIndices.length; vertex++) {
        const sourceOffset = lineSourceIndices[vertex] * 3;
        const lineOffset = vertex * 3;
        lineDisplacement[lineOffset] = sourceDisplacement[sourceOffset];
        lineDisplacement[lineOffset + 1] = sourceDisplacement[sourceOffset + 1];
        lineDisplacement[lineOffset + 2] = sourceDisplacement[sourceOffset + 2];
    }
    installResultLineSegments(
        mesh,
        new Float32Array(linePositions),
        new Float32Array(lineColors),
        lineDisplacement,
        segmentRangeIds(lineVertexLabels),
    );
    // Re-apply whatever was selected before this repaint: applying a field
    // rebuilds the lines from scratch, so a selection made beforehand would
    // otherwise vanish on the next step or component change.
    const restore = mesh.userData.__feaLineSelection as
        | ((ids: readonly string[]) => void)
        | undefined;
    if (restore) {
        const owner = mesh as unknown as {selectedRanges?: Set<string>};
        const current = Array.from(owner.selectedRanges ?? []);
        if (current.length) restore(current);
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

