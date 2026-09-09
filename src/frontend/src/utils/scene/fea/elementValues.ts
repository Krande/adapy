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
    FeaManifestField,
    FeaManifestFieldPerType,
} from "@/services/viewerApi";
import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {makeViewerApiFetcher} from "@/services/feaFieldBlob";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {layerIpIndices, reduceIps} from "./applyElemField";

/** One component's value for the selected element. */
export interface ElementComponentValue {
    component: string;
    /** NaN where the element carries no value for this component. */
    value: number;
}

/** One attribute's values for the selected element. */
export interface ElementFieldValues {
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

export interface ElementValuesResult {
    /** The label asked for, as given. */
    label: string;
    /** Empty when the element is in no bucket of any element field. */
    fields: ElementFieldValues[];
    /** Step the values were read at, and its label, for display. */
    stepIndex: number;
    stepLabel: string | null;
}

/** Strip the display prefix a 3D pick puts on a draw-range label. */
export function elementLabelNumber(label: string): number | null {
    const m = /^[Ee]?(\d+)$/.exec(label.trim());
    return m ? Number(m[1]) : null;
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
        const index = bucket.element_labels.indexOf(elementId);
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
export async function feaValuesForElement(
    label: string,
    scope: ElementValueScope = "active",
): Promise<ElementValuesResult> {
    const state = useFeaAnimationStore.getState();
    const {manifest, sourceName, stepIndex, layer, ipReduction, fieldName} = state;
    const empty: ElementValuesResult = {label, fields: [], stepIndex, stepLabel: null};
    if (!manifest || !sourceName) return empty;

    const elementId = elementLabelNumber(label);
    if (elementId === null) return empty;

    const wanted = (manifest.fields ?? []).filter((f) => {
        if (!f.per_type?.length) return false;
        if (scope === "properties") return f.category === "property";
        if (scope === "all") return true;
        // The active attribute, and its surface variants — picking Lower swaps
        // the loaded field name, and the user still means the same attribute.
        if (f.name_canonical === fieldName) return true;
        return (f.surface_variants ?? []).some((v) => v.field_name === fieldName);
    });
    if (!wanted.length) return empty;

    const urlScope = scopeUrlPart(useScopeStore.getState().current);
    const {fetcher, rangeFetcher, cacheKey} = makeViewerApiFetcher(urlScope, sourceName);

    const out: ElementFieldValues[] = [];
    await Promise.all(
        wanted.map(async (field) => {
            const found = locate(field, elementId);
            if (!found) return;
            const {bucket, index} = found;
            const step = Math.min(stepIndex, Math.max(0, (field.n_steps ?? 1) - 1));
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

    const steps = wanted[0]?.steps;
    const stepLabel = steps?.[stepIndex]?.name ?? steps?.[stepIndex]?.label ?? null;
    return {label, fields: out, stepIndex, stepLabel};
}
