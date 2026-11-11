// One step of an element (integration point) field as plain numbers.
//
// This is the computation behind a painted element field, with nothing of a renderer in it: the
// step's values per element-type bucket go in, and out come a colour per render vertex, the
// un-scaled displacement per node, the result-point markers and the coloured lines of the line
// elements (positions, colours and displacement each), and the value every element was painted
// with. Whichever viewport is on screen draws them: the three.js one through applyElemField.ts,
// the three.js-free one through result layers (streaming/layerSession.ts).
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
//                                draw range gets that RGB.
//
// An element's colour is its own, so the mesh it is painted on is element-local: every corner of
// every triangle is a vertex of its own (``elementLocalVertices``), and ``renderToSource`` says
// which node each one is. Smooth shading (``nodalAverage``) paints the mesh in whatever numbering
// it has.
//
// No three.js in this module or in anything it imports: the three.js-free viewport uses it too.

import type {FeaManifestField, FeaManifestFieldPerType, FeaScalarRange} from "@/services/viewerApi";
import {getColormap, linearColormap} from "./colormaps";
import {bandedColormap, resolveContourRange, type ContourSettings} from "./contourScale";
import {categoryCodes, distinctValues, isPropertyField, propertyColormap} from "./propertyColors";
import {RangeAccumulator} from "./rangeAccumulator";
import {PaintedValues} from "./visibleValues";
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
    const index = field.components.indexOf(reduction);
    // A one-component field's reduction is the bake's "scalar" (default_view for any
    // field under three components without presentation metadata) rather than the
    // component's own name. It means "the value itself": component 0. Without this
    // every such field fell through to the 0 fallback and painted uniformly at the
    // bottom of its scale.
    if (index < 0 && field.components.length === 1) return 0;
    return index;
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

/** One scalar from an element's ``(n_ips × n_components)`` block, for the given IPs:
 *  the IP reduction per component, then the component reduction. NaN where the
 *  attribute does not apply to the element. */
export type ElementScalarReader = (stepView: Float32Array, elemBase: number, ipIndices: number[]) => number;

export function elementScalarReader(
    field: FeaManifestField,
    reduction: string,
    ipReduction: string,
): ElementScalarReader {
    const n_components = field.components.length;
    const isMagnitude = reduction === "magnitude";
    const compIdx = isMagnitude ? -1 : componentIndex(field, reduction);
    return (stepView, elemBase, ipIndices) => {
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
}

/**
 * The range of what an element field paints on the elements ``isShown`` names, or
 * null when none of them carries a value (see shownRange.ts).
 *
 * The values are the ones the painter colours: each element's reduced scalar, and,
 * for a field painted per corner or per result point, each of those values too.
 * Nodal averaging only ever narrows these, so the range still covers it.
 */
export function shownElemFieldRange(args: {
    field: FeaManifestField;
    perTypeStepValues: Float32Array[];
    layer: string;
    ipReduction: string;
    reduction: string;
    isShown: (label: number) => boolean;
}): [number, number] | null {
    const {field, perTypeStepValues, layer, ipReduction, reduction, isShown} = args;
    const read = elementScalarReader(field, reduction, ipReduction);
    const perIp =
        field.support === "element_nodal"
        || field.support === "result_point"
        || field.support === "line_result_point";
    const n_components = field.components.length;
    const acc = new RangeAccumulator();
    const buckets = field.per_type ?? [];
    for (let b = 0; b < buckets.length; b++) {
        const bucket = buckets[b];
        const stepView = perTypeStepValues[b];
        if (!stepView) continue;
        const ipIndices = layerIpIndices(bucket, layer);
        const elemStride = bucket.n_ips * n_components;
        const labels = bucketLabels(field, bucket);
        for (let e = 0; e < bucket.n_elements; e++) {
            if (!isShown(labels[e])) continue;
            const elemBase = e * elemStride;
            acc.add(read(stepView, elemBase, ipIndices));
            if (perIp) for (const ip of ipIndices) acc.add(read(stepView, elemBase, [ip]));
        }
    }
    return acc.result();
}

/** A bucket's element labels, which a split manifest (bake_version 6) keeps in
 *  the field's detail sidecar: the caller resolves the field through
 *  ``withFieldDetail`` first (paintElemField does). Missing here is a caller
 *  bug, and painting every element as label ``undefined`` would hide it. */
interface BucketPlan {
    /** "E<label>" per element: the id the mesh draws and hides it by. */
    keys: string[];
    /** Its draw range in the index buffer, or count -1 when it has none (a line). */
    start: Int32Array;
    count: Int32Array;
}

const bucketPlans = new WeakMap<Map<string, [number, number]>, WeakMap<readonly number[], BucketPlan>>();

/** Each element's draw range and id, resolved once per (mesh draw ranges, bucket
 *  labels) -- both stay the same objects for as long as one source is loaded. */
function bucketPlan(drawRanges: Map<string, [number, number]>, labels: readonly number[]): BucketPlan {
    let byLabels = bucketPlans.get(drawRanges);
    if (!byLabels) {
        byLabels = new WeakMap();
        bucketPlans.set(drawRanges, byLabels);
    }
    const cached = byLabels.get(labels);
    if (cached) return cached;
    const n = labels.length;
    const plan: BucketPlan = {keys: new Array<string>(n), start: new Int32Array(n), count: new Int32Array(n)};
    for (let e = 0; e < n; e++) {
        const key = `E${labels[e]}`;
        plan.keys[e] = key;
        const dr = drawRanges.get(key);
        plan.start[e] = dr ? dr[0] : 0;
        plan.count[e] = dr ? dr[1] : -1;
    }
    byLabels.set(labels, plan);
    return plan;
}

function bucketLabels(field: FeaManifestField, bucket: FeaManifestFieldPerType): number[] {
    if (!bucket.element_labels) {
        throw new Error(
            `field ${field.name_canonical} bucket ${bucket.elem_type}: element labels not loaded ` +
            `(resolve the field with withFieldDetail first)`,
        );
    }
    return bucket.element_labels;
}

/** Every corner of every triangle as a vertex of its own: the node each one is (which is the
 *  index list itself), and the index list that draws them in order. Element draw ranges stay
 *  valid, because the new index list is exactly as long as the old one. */
export function elementLocalVertices(indices: ArrayLike<number>): {renderToSource: Uint32Array; indices: Uint32Array} {
    const renderToSource = Uint32Array.from(indices);
    const sequential = new Uint32Array(renderToSource.length);
    for (let i = 0; i < sequential.length; i++) sequential[i] = i;
    return {renderToSource, indices: sequential};
}

/** `source` (three floats per node) as three floats per render vertex, into `out`. */
export function expandTriplesInto(source: Float32Array, renderToSource: Uint32Array, out: Float32Array): void {
    for (let render = 0, o = 0; render < renderToSource.length; render++, o += 3) {
        const s = renderToSource[render] * 3;
        out[o] = source[s];
        out[o + 1] = source[s + 1];
        out[o + 2] = source[s + 2];
    }
}

export interface ElemFieldPaint {
    /** Undeformed node positions, xyz per node. */
    basePositions: Float32Array;
    /** The node each render vertex is: the identity for a mesh in its own numbering. */
    renderToSource: Uint32Array;
    /** The mesh's index list, in render vertices. */
    indices: ArrayLike<number>;
    /** ``E<label>`` -> ``[start, count]`` in ``indices``, for every element that has triangles. */
    drawRanges: Map<string, [number, number]>;
    /** The element field driving the colour. Must have ``per_type`` populated. */
    colorField: FeaManifestField;
    /** One ``(n_elements * n_ips * n_components)`` Float32 view per per_type bucket, in the same
     *  order as ``colorField.per_type``. */
    perTypeStepValues: Float32Array[];
    /** Layer filter: "top" | "bottom" | "mid" | "all". */
    layer: string;
    /** IP reduction: "max_abs" | "mean" | "max" | "min". */
    ipReduction: string;
    /** "magnitude" or one of ``colorField.components``. */
    reduction: string;
    /** The (nodal) field driving the displacement; omit to leave the shape undeformed. */
    warpField?: FeaManifestField;
    warpStepValues?: Float32Array;
    colormap?: string;
    /** How the scale is drawn: discrete bands, and either end of the range pinned. */
    contour?: ContourSettings | null;
    /** The range the scale follows where ``contour`` pins no end. Omitted: the field's
     *  ``scalar_range``. Ignored for a model property. */
    autoRange?: readonly [number, number] | null;
    /** Build the coloured two-vertex lines of the line elements. */
    lineFallback?: boolean;
    /** Average each node's element values instead of painting each element in its own colour. */
    nodalAverage?: boolean;
    /** Build the result-point markers of a field that has result points. False for a mesh that
     *  only repeats the elements of another (the beam solids). */
    resultPoints?: boolean;
}

/** Points drawn beside the faces: where they are, their colour and their un-scaled displacement,
 *  three floats per point each. */
export interface PaintedPoints {
    positions: Float32Array;
    colors: Float32Array;
    displacement: Float32Array;
    /** The element each point belongs to, where a viewport draws the points as its elements. */
    labels?: readonly number[];
}

export interface ElemFieldPainted {
    /** What each element was painted with, by the id the mesh draws it by. */
    values: PaintedValues;
    /** The un-scaled displacement, xyz per node (all zero without a warp field). */
    sourceDisplacement: Float32Array;
    /** One marker per result point. */
    markers: PaintedPoints;
    /** Two points per line element. */
    lines: PaintedPoints;
    /** The element label of each line point, beside the point itself. */
    lineVertexLabels: number[];
}

/**
 * Paint one step. `outColors` (three floats per render vertex) is overwritten in full; every
 * bucket's length is checked before it is touched, so a bad step throws with the previous
 * frame's colours intact.
 */
export function paintElemField(args: ElemFieldPaint, outColors: Float32Array): ElemFieldPainted {
    const {
        basePositions,
        renderToSource,
        indices: indexArr,
        drawRanges,
        colorField,
        perTypeStepValues,
        layer,
        ipReduction,
        reduction,
        warpField,
        warpStepValues,
        colormap: colormapName,
        contour,
        autoRange,
        nodalAverage: nodalAverageArg = false,
        lineFallback = true,
        resultPoints = true,
    } = args;
    // A model property is not a result: it is drawn in its own scheme over its own
    // range (propertyColors.ts), never averaged across element boundaries - the
    // mean of two material ids is a third material - and the result scale's bands
    // and pinned ends do not apply to it.
    const property = isPropertyField(colorField);
    const nodalAverage = nodalAverageArg && !property;

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

    const [rangeMin, rangeMax] = property
        ? pickRange(colorField, reduction)
        : resolveContourRange(autoRange ?? pickRange(colorField, reduction), contour);
    // A numeric property is coloured by rank among the values it carries, so the
    // painter needs them before it paints: one pass over the step views, which for
    // a property are one value per element.
    const levels =
        property && categoryCodes(colorField).length === 0
            ? distinctValues((function* () {
                  for (const view of perTypeStepValues) yield* view;
              })())
            : null;
    // Linear light: a viewport draws the legend's colour only when it is handed that (colormaps.ts).
    const colormap = linearColormap(
        propertyColormap(colorField, [rangeMin, rangeMax], levels) ??
        bandedColormap(getColormap(colormapName), contour?.levels ?? null),
    );
    const n_points = basePositions.length / 3;
    const n_render_points = renderToSource.length;
    const n_components = colorField.components.length;
    const out_colors = outColors;

    // Every bucket's length is checked before the buffer on screen is touched, so
    // a bad step throws with the previous frame still intact.
    for (let b = 0; b < colorField.per_type.length; b++) {
        const bucket = colorField.per_type[b];
        const expected = bucket.n_elements * bucket.n_ips * colorField.components.length;
        if (perTypeStepValues[b]?.length !== expected) {
            throw new Error(
                `applyElemFieldToMesh: bucket ${bucket.elem_type} step view ` +
                `length ${perTypeStepValues[b]?.length} != expected ${expected}`,
            );
        }
    }
    // Vertex colour seed: medium grey for any vertex not covered by an
    // element draw range. Without this seed, the colours would be whatever the
    // previous step left (or black) where no element reaches (typically nowhere on
    // a healthy mesh, but defensive).
    out_colors.fill(0.5);

    const valueByRange = new PaintedValues();
    const range = rangeMax - rangeMin;
    const scaleColor = range > 0 ? 1 / range : 0;

    const tmpRgb = new Float32Array(3);

    // Compute one scalar per element from the (n_ips × n_components)
    // block. Same logic for both flat and smooth render paths — only
    // the downstream "where does the scalar land" step differs.
    const computeElementScalar = elementScalarReader(colorField, reduction, ipReduction);

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

    // Per-bucket loop. Each bucket is one element type; the draw-range map
    // collapses across types so a single lookup works regardless of which
    // bucket the label came from.
    for (let b = 0; b < colorField.per_type.length; b++) {
        const bucket = colorField.per_type[b];
        const stepView = perTypeStepValues[b];
        const ipIndices = layerIpIndices(bucket, layer);
        const elemStride = bucket.n_ips * n_components;
        const labels = bucketLabels(colorField, bucket);
        // Label -> draw range and id, resolved once per mesh and bucket rather than
        // by building and hashing an "E<label>" string per element per frame.
        const plan = bucketPlan(drawRanges, labels);
        // What each element ended up painted with, so a legend can say which
        // values are on screen rather than which values the deck contains. One
        // entry per element, keyed by the id the mesh hides by.
        const painted = new Float64Array(bucket.n_elements).fill(NaN);
        valueByRange.add(plan.keys, painted);

        for (let e = 0; e < bucket.n_elements; e++) {
            const elemBase = e * elemStride;
            const label = labels[e];
            const vCount = plan.count[e];
            const hasDr = vCount >= 0;
            const vStart = plan.start[e];
            {
                const own = computeElementScalar(stepView, elemBase, ipIndices);
                if (isFinite(own)) painted[e] = own;
            }

            // Line elements have draw ranges with zero triangles, so their
            // values cannot colour the face mesh. Draw a two-vertex segment
            // from the manifest connectivity as the no-solid fallback. For
            // In Elements fields, the two result slots map to the beam
            // ends; element-average fields use one colour at both ends.
            if (
                lineFallback
                && !hasDr
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
                    // A segment's pair must be complete. Drop a lone end
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
                resultPoints
                && (
                    colorField.support === "result_point"
                    || colorField.support === "line_result_point"
                )
            ) {
                const sourceCorners = bucket.element_node_indices?.[e]?.slice() ?? [];
                if (sourceCorners.length === 0 && hasDr) {
                    const seenSources = new Set<number>();
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

            if (!hasDr) continue;

            // In Elements fields, carry one value per element-local corner.
            // Preserve that variation instead of collapsing all corners through
            // the generic IP reducer. The first-seen vertex order of the element's
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
        // touched them — line-only verts or gaps in the draw ranges). For
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
    const lineDisplacement = new Float32Array(lineSourceIndices.length * 3);
    for (let vertex = 0; vertex < lineSourceIndices.length; vertex++) {
        const sourceOffset = lineSourceIndices[vertex] * 3;
        const lineOffset = vertex * 3;
        lineDisplacement[lineOffset] = sourceDisplacement[sourceOffset];
        lineDisplacement[lineOffset + 1] = sourceDisplacement[sourceOffset + 1];
        lineDisplacement[lineOffset + 2] = sourceDisplacement[sourceOffset + 2];
    }
    return {
        values: valueByRange,
        sourceDisplacement,
        markers: {
            positions: new Float32Array(markerPositions),
            colors: new Float32Array(markerColors),
            displacement: markerDisplacement,
        },
        lines: {
            positions: new Float32Array(linePositions),
            colors: new Float32Array(lineColors),
            displacement: lineDisplacement,
        },
        lineVertexLabels,
    };
}
