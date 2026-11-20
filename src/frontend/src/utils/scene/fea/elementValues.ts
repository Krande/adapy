// What the model is actually showing, for one element, as numbers.
//
// The scene answers "which elements are hot" well and "what is this element's
// stress" not at all: the colour is the only readout, and reading a number off a
// colour bar is not a comparison you can take to a report. This resolves a
// picked element to the same values the colouring used.
//
// "The same" is load-bearing. It reuses `layerIpIndices` and `reduceIps` from
// applyElemField rather than re-deriving them, so the number in a panel is by
// construction the number that was painted — including the layer filter and the
// IP reduction the user has selected. A second implementation would drift, and
// a readout that disagrees with the picture is worse than no readout.

import type {
    FeaManifest,
    FeaManifestField,
    FeaManifestFieldPerType,
} from "@/services/viewerApi";
import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {fetchFieldStep} from "@/services/feaFieldBlob";
import {elementNodes, manifestNodeLabels, withFieldDetail} from "@/services/fea/feaFieldDetail";
import {capabilities} from "@/services/capabilities";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {peekCase, resolveCase} from "@/services/fea/feaCaseResolver";
import {caseFieldView, isCaseRef} from "@/services/fea/feaStepRef";
import {layerIpIndices, reduceIps} from "./elemFieldPaint";
import {mergeCaseSteps, slotRef} from "./caseSteps";
import {feaSourceScope} from "./streaming/session";

/** One component's value for the selected element. */
export interface ElementComponentValue {
    component: string;
    /** NaN where the element carries no value for this component. */
    value: number;
}

/** One attribute's values for the selected element. */
export interface ElementFieldValues {
    kind: "element";
    field: FeaManifestField;
    /** The element-type bucket the element was found in ("quad", "line", …). */
    elemType: string;
    /**
     * The layer the reduction actually ran over.
     *
     * Not always the one asked for: `layerIpIndices` falls back to every IP when
     * the bucket has no such layer, which is right -- better a value than a grey
     * element -- but a caller that echoes the REQUESTED layer then labels the
     * number with a layer it was not read from.
     */
    layerUsed: string;
    components: ElementComponentValue[];
}

/** Which attributes to read. "properties" reads only the model-property
 *  fields (category "property": thickness, material, section) — what a
 *  Properties panel shows about a picked element regardless of the active
 *  result selection. */
export type ElementValueScope = "active" | "all" | "properties";

/** A nodal attribute's values at some nodes (a picked element's, or one node): one row per node,
 *  its values in the order of `components`, at the step the colouring shows. */
export interface NodalFieldValues {
    kind: "nodal";
    field: FeaManifestField;
    /** Where the values are: the field's surface when it has one, else its support ("nodal"). */
    position: string;
    /** The attribute, as the picker names it (`field.name_canonical`). */
    attribute: string;
    unit: string | null;
    components: string[];
    /** `node`: the solver's node id (the point index when the bake has no ids). NaN where the
     *  field has no value. */
    nodes: {node: number; values: number[]}[];
    stepIndex: number;
    stepLabel: string | null;
}

/** An element attribute's values for an element, or a nodal one's at its nodes (`kind`). */
export type FeaFieldValues = ElementFieldValues | NodalFieldValues;

/** `nodal`: also read the nodal attributes `scope` names, at the element's nodes. */
export interface FeaValuesOptions {
    nodal?: boolean;
}

/** `F`: what `fields` holds -- element fields (the default, without `nodal`), or element and nodal
 *  ones (`ElementValuesResult<FeaFieldValues>`, with `nodal`). */
export interface ElementValuesResult<F extends FeaFieldValues = ElementFieldValues> {
    /** The label asked for, as given. */
    label: string;
    /** Empty when the element is in no bucket of any element field (and, with `nodal`, no nodal
     *  attribute reaches it). Element fields first, then nodal ones, each in manifest order. */
    fields: F[];
    /** Step the values were read at, and its label, for display. */
    stepIndex: number;
    stepLabel: string | null;
}

/** Strip the display prefix a 3D pick puts on a draw-range label. */
export function elementLabelNumber(label: string): number | null {
    const m = /^[Ee]?(\d+)$/.exec(label.trim());
    return m ? Number(m[1]) : null;
}

/**
 * The field and step to read for slot `stepIndex` of `baseField`: the case's own blobs for a
 * combination slot (the ones the colouring read), the stored step (clamped) otherwise, with the
 * field's detail sidecar (the labels to find an element by) resolved on a split bake. Null where
 * the case or the sidecar cannot be had. Shared by every reader of a result's per-element values.
 */
export async function resolveFieldSlot(
    manifest: NonNullable<ReturnType<typeof useFeaAnimationStore.getState>["manifest"]>,
    sourceName: string,
    baseField: FeaManifestField,
    stepIndex: number,
    urlScope: ReturnType<typeof feaSourceScope>,
    fetcher: Parameters<typeof withFieldDetail>[1],
    cacheKey: Parameters<typeof withFieldDetail>[2],
): Promise<{field: FeaManifestField; step: number} | null> {
    let field = baseField;
    let step: number;
    const ref = slotRef(manifest, baseField, stepIndex);
    if (isCaseRef(ref)) {
        const resolution = peekCase(manifest, sourceName, ref.case)
            ?? (await resolveCase(manifest, {scope: urlScope, sourceKey: sourceName}, ref.case).catch(() => null));
        const caseView = resolution ? caseFieldView(baseField, resolution.overlay, resolution.relPrefix) : null;
        if (!caseView) return null;
        field = caseView;
        step = 0;
    } else {
        step = Math.min(ref.stored, Math.max(0, (baseField.n_steps ?? 1) - 1));
    }
    try {
        field = await withFieldDetail(field, fetcher, cacheKey);
    } catch {
        return null;
    }
    return {field, step};
}

/** The bucket holding this element, and its index within it. */
function locate(
    field: FeaManifestField,
    elementId: number,
): {bucket: FeaManifestFieldPerType; index: number} | null {
    for (const bucket of field.per_type ?? []) {
        // Labels are ascending in every bake, but not guaranteed to be, and a
        // linear scan over a few thousand is not what makes this slow — the
        // fetch is. Correctness over a binary search that a future reader would
        // have to prove.
        const index = bucket.element_labels?.indexOf(elementId) ?? -1;
        if (index >= 0) return {bucket, index};
    }
    return null;
}

/**
 * Read one element's values for the active attribute, or for every attribute.
 *
 * `"all"` is one fetch per element field. They are cached and shared with the
 * colouring, so the attribute already on screen costs nothing and the rest cost
 * one blob each, once per step. That is the price of not making the user click
 * twenty attributes to read twenty numbers, and it is why the caller decides.
 */
export async function feaValuesForElement(label: string, scope?: ElementValueScope, options?: {nodal?: false}): Promise<ElementValuesResult>;
export async function feaValuesForElement(label: string, scope: ElementValueScope, options: {nodal: true}): Promise<ElementValuesResult<FeaFieldValues>>;
export async function feaValuesForElement(
    label: string,
    scope: ElementValueScope = "active",
    options: FeaValuesOptions = {},
): Promise<ElementValuesResult<FeaFieldValues>> {
    const state = useFeaAnimationStore.getState();
    const {manifest, sourceName, stepIndex, layer, ipReduction, fieldName} = state;
    const empty: ElementValuesResult<FeaFieldValues> = {label, fields: [], stepIndex, stepLabel: null};
    if (!manifest || !sourceName) return empty;

    const elementId = elementLabelNumber(label);
    if (elementId === null) return empty;

    const wanted = (manifest.fields ?? []).filter((f) => !!f.per_type?.length && wantedField(f, scope, fieldName));
    const nodal = options.nodal ? nodalFieldsFor(manifest.fields ?? [], scope, fieldName) : [];
    if (!wanted.length && !nodal.length) return empty;

    // Where the open model's blobs are, not where the storage browser is.
    const urlScope = feaSourceScope(sourceName);
    const {fetcher, rangeFetcher, cacheKey} = capabilities.fea.fieldFetchers(urlScope, sourceName);

    const out: ElementFieldValues[] = [];
    await Promise.all(
        wanted.map(async (baseField) => {
            const slot = await resolveFieldSlot(manifest, sourceName, baseField, stepIndex, urlScope, fetcher, cacheKey);
            if (!slot) return;
            const {field, step} = slot;
            const found = locate(field, elementId);
            if (!found) return;
            const {bucket, index} = found;
            let view: Float32Array;
            try {
                view = await fetchElemFieldStep(rangeFetcher, fetcher, bucket, step, cacheKey);
            } catch {
                // One unreadable blob must not blank the other nineteen.
                return;
            }
            // The blob's component count is the FIELD's, not something the
            // per-type bucket carries -- applyElemField reads it the same way.
            // Taking it from the bucket yields undefined, which makes every
            // offset NaN and every value read back as "no value".
            const nComp = field.components.length;
            const ips = layerIpIndices(bucket, layer);
            const hasLayer = (bucket.ip_layout ?? []).some((e) => e.layer === layer);
            const base = index * bucket.n_ips * nComp;
            out.push({
                kind: "element",
                field,
                elemType: bucket.elem_type,
                layerUsed: hasLayer ? layer : "all",
                components: field.components.map((component, c) => ({
                    component,
                    value: reduceIps(view, base, ips, nComp, c, ipReduction),
                })),
            });
        }),
    );

    // Manifest order, not completion order — Promise.all resolves in whatever
    // order the fetches land, and a panel whose rows reorder between clicks is
    // unreadable.
    const rank = new Map((manifest.fields ?? []).map((f, i) => [f.name_canonical, i]));
    out.sort((a, b) => (rank.get(a.field.name_canonical) ?? 0) - (rank.get(b.field.name_canonical) ?? 0));

    const slot = mergeCaseSteps(manifest, wanted[0] ?? nodal[0] ?? null)[stepIndex];
    const stepLabel = slot?.name ?? slot?.label ?? null;
    if (!nodal.length) return {label, fields: out, stepIndex, stepLabel};
    // The nodal attributes at the element's nodes: the points its connectivity names.
    const found = await elementNodes(manifest, elementId, fetcher, cacheKey).catch(() => null);
    const points = found ? found.indices.map((index, i) => ({index, node: found.nodes[i]})) : [];
    const atNodes = points.length
        ? await nodalValues(nodal, points, stepIndex, {manifest, sourceName, urlScope, fetcher, rangeFetcher, cacheKey})
        : [];
    return {label, fields: [...out, ...atNodes], stepIndex, stepLabel};
}

/** Is `f` one of the attributes `scope` reads: the displayed one (and its surface variants --
 *  picking Lower swaps the loaded field name, and the user still means the same attribute), the
 *  property fields, or all of them. */
function wantedField(f: FeaManifestField, scope: ElementValueScope, fieldName: string | null): boolean {
    if (scope === "properties") return f.category === "property";
    if (scope === "all") return true;
    if (f.name_canonical === fieldName) return true;
    return (f.surface_variants ?? []).some((v) => v.field_name === fieldName);
}

/** The nodal attributes (one blob per field, values per point) `scope` reads; none for "properties". */
function nodalFieldsFor(fields: readonly FeaManifestField[], scope: ElementValueScope, fieldName: string | null): FeaManifestField[] {
    if (scope === "properties") return [];
    return fields.filter((f) => !f.per_type?.length && !!f.blob && wantedField(f, scope, fieldName));
}

type Fetchers = ReturnType<typeof capabilities.fea.fieldFetchers>;

interface NodalSource {
    manifest: FeaManifest;
    sourceName: string;
    urlScope: string;
    fetcher: Fetchers["fetcher"];
    rangeFetcher: Fetchers["rangeFetcher"];
    cacheKey: string;
}

/** `fields`' values at `points` for slot `stepIndex`: the step blob the colouring reads (one range
 *  fetch per field, cached and shared with it), one row per point. A field whose blob cannot be read
 *  is left out; the others still answer. */
async function nodalValues(
    fields: readonly FeaManifestField[],
    points: readonly {index: number; node: number}[],
    stepIndex: number,
    src: NodalSource,
): Promise<NodalFieldValues[]> {
    const out: NodalFieldValues[] = [];
    await Promise.all(
        fields.map(async (baseField) => {
            const slot = await resolveFieldSlot(src.manifest, src.sourceName, baseField, stepIndex, src.urlScope, src.fetcher, src.cacheKey);
            if (!slot) return;
            let view: Float32Array;
            try {
                view = await fetchFieldStep(src.rangeFetcher, src.fetcher, slot.field, slot.step, src.cacheKey);
            } catch {
                return;
            }
            const nComp = baseField.components.length;
            const caseSlot = mergeCaseSteps(src.manifest, baseField)[stepIndex];
            out.push({
                kind: "nodal",
                field: baseField,
                position: baseField.surface ?? baseField.support,
                attribute: baseField.name_canonical,
                unit: baseField.unit ?? null,
                components: [...baseField.components],
                nodes: points.map(({index, node}) => ({
                    node,
                    values: Array.from({length: nComp}, (_, c) => view[index * nComp + c] ?? Number.NaN),
                })),
                stepIndex,
                stepLabel: caseSlot?.name ?? caseSlot?.label ?? null,
            });
        }),
    );
    const rank = new Map((src.manifest.fields ?? []).map((f, i) => [f.name_canonical, i]));
    return out.sort((a, b) => (rank.get(a.attribute) ?? 0) - (rank.get(b.attribute) ?? 0));
}

/** What `feaValuesForNode` reads: the nodal attributes at one node. */
export interface NodeValuesResult {
    /** The node id asked for. */
    node: number;
    /** Empty when no nodal attribute is read, or the node is not in the model. */
    fields: NodalFieldValues[];
    stepIndex: number;
    stepLabel: string | null;
}

/**
 * One node's values of the displayed nodal attribute (`"active"`) or of every nodal one (`"all"`),
 * at the step the colouring shows: what `feaValuesForElement(..., {nodal: true})` reads at an
 * element's nodes, for one node. `nodeLabel`: the solver's node id (12 or "N12"); the point index
 * when the bake has no node ids.
 */
export async function feaValuesForNode(nodeLabel: string | number, scope: ElementValueScope = "active"): Promise<NodeValuesResult> {
    const {manifest, sourceName, stepIndex, fieldName} = useFeaAnimationStore.getState();
    const node = typeof nodeLabel === "number" ? nodeLabel : Number(String(nodeLabel).replace(/^N/i, ""));
    const empty: NodeValuesResult = {node, fields: [], stepIndex, stepLabel: null};
    if (!manifest || !sourceName || !Number.isInteger(node)) return empty;
    const fields = nodalFieldsFor(manifest.fields ?? [], scope, fieldName);
    if (!fields.length) return empty;
    const urlScope = feaSourceScope(sourceName);
    const {fetcher, rangeFetcher, cacheKey} = capabilities.fea.fieldFetchers(urlScope, sourceName);
    const labels = await manifestNodeLabels(manifest, fetcher, cacheKey).catch(() => null);
    const index = labels ? labels.indexOf(node) : node;
    if (index < 0) return empty;
    const values = await nodalValues(fields, [{index, node}], stepIndex, {manifest, sourceName, urlScope, fetcher, rangeFetcher, cacheKey});
    return {node, fields: values, stepIndex, stepLabel: values[0]?.stepLabel ?? null};
}
