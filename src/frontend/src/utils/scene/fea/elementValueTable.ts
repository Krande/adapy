// Per-element values of a loaded result, for labelling (`layout/NumberLabels`, "property value"
// labels): the value each element has of the shown result field, or of a model property (section,
// material, thickness), as short text.
//
// The same reads the colouring and the Selected Object panel make -- `resolveFieldSlot`,
// `layerIpIndices`, `reduceIps` -- so a label is by construction the number that was painted,
// layer filter and IP reduction included. One blob per element bucket, fetched once and kept for
// the last (field, step, layer, reduction) asked for; a lookup is a binary search.

import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {capabilities} from "@/services/capabilities";
import type {FeaManifestField} from "@/services/viewerApi";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {layerIpIndices, reduceIps} from "./applyElemField";
import {resolveFieldSlot} from "./elementValues";
import {feaSourceScope} from "./streaming/session";

/** The values of one field for the elements of a result. */
export interface ElementValueTable {
    /** The field the values are of (its canonical name). */
    field: string;
    /** The component the values are of ("magnitude" for a vector's length). */
    component: string;
    /** An element's value as label text; undefined where the element has none. */
    text(elementNumber: number): string | undefined;
}

/** A value as a short label: four significant digits, an exponent where plain digits would be long. */
export function valueLabelText(v: number): string | undefined {
    if (!Number.isFinite(v)) return undefined;
    if (v === 0) return "0";
    const a = Math.abs(v);
    if (a >= 1e5 || a < 1e-3) return v.toExponential(2);
    return String(Number(v.toPrecision(4)));
}

interface Bucket {
    labels: ArrayLike<number>;
    values: Float32Array;
    /** Labels ascend: a lookup is a binary search; else through `byLabel`. */
    sorted: boolean;
    byLabel?: Map<number, number>;
}

function lookup(b: Bucket, label: number): number | undefined {
    if (b.sorted) {
        let lo = 0;
        let hi = b.labels.length - 1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const l = b.labels[mid];
            if (l === label) return b.values[mid];
            if (l < label) lo = mid + 1;
            else hi = mid - 1;
        }
        return undefined;
    }
    if (!b.byLabel) {
        b.byLabel = new Map();
        for (let i = 0; i < b.labels.length; i++) b.byLabel.set(b.labels[i], i);
    }
    const i = b.byLabel.get(label);
    return i === undefined ? undefined : b.values[i];
}

function ascending(labels: ArrayLike<number>): boolean {
    for (let i = 1; i < labels.length; i++) if (labels[i] <= labels[i - 1]) return false;
    return true;
}

/** The manifest's element field named `name` (or one of whose surface variants is), when it holds per-element values. */
export function elementValueField(name: string | null | undefined): FeaManifestField | null {
    const {manifest} = useFeaAnimationStore.getState();
    if (!manifest || !name) return null;
    const f = manifest.fields.find((x) => x.name_canonical === name || (x.surface_variants ?? []).some((v) => v.field_name === name));
    return f?.per_type?.length ? f : null;
}

let cacheKey = "";
let cached: Promise<ElementValueTable | null> | null = null;

/**
 * The per-element values of `fieldName` (default: the field the result shows) at the shown step,
 * layer and reduction. Null where there are none: no result, a nodal field (no value per
 * element), a field the manifest does not carry.
 */
export function loadElementValueTable(fieldName?: string | null): Promise<ElementValueTable | null> {
    const st = useFeaAnimationStore.getState();
    const name = fieldName ?? st.fieldName;
    const key = [st.sourceName, name, st.stepIndex, st.layer, st.ipReduction, st.reduction].join("|");
    if (cached && key === cacheKey) return cached;
    cacheKey = key;
    cached = read(name).catch(() => null);
    return cached;
}

/** Forget the table (a result unloaded; tests). */
export function resetElementValueTable(): void {
    cacheKey = "";
    cached = null;
}

async function read(name: string | null): Promise<ElementValueTable | null> {
    const st = useFeaAnimationStore.getState();
    const {manifest, sourceName, stepIndex, layer, ipReduction, reduction} = st;
    const baseField = elementValueField(name);
    if (!manifest || !sourceName || !baseField) return null;
    const urlScope = feaSourceScope(sourceName);
    const {fetcher, rangeFetcher, cacheKey: blobKey} = capabilities.fea.fieldFetchers(urlScope, sourceName);
    const slot = await resolveFieldSlot(manifest, sourceName, baseField, stepIndex, urlScope, fetcher, blobKey);
    if (!slot) return null;
    const {field, step} = slot;
    const nComp = field.components.length;
    if (!nComp) return null;
    const asked = field.components.indexOf(reduction);
    // A named component, else the length of the vector ("magnitude"), else the first.
    const magnitude = asked < 0 && reduction === "magnitude" && nComp > 1;
    const comp = asked >= 0 ? asked : 0;
    const buckets: Bucket[] = [];
    for (const bucket of field.per_type ?? []) {
        const labels = bucket.element_labels;
        if (!labels?.length) continue;
        let view: Float32Array;
        try {
            view = await fetchElemFieldStep(rangeFetcher, fetcher, bucket, step, blobKey);
        } catch {
            continue;
        }
        const ips = layerIpIndices(bucket, layer);
        const values = new Float32Array(labels.length);
        for (let i = 0; i < labels.length; i++) {
            const base = i * bucket.n_ips * nComp;
            if (magnitude) {
                let sq = 0;
                for (let c = 0; c < nComp; c++) {
                    const v = reduceIps(view, base, ips, nComp, c, ipReduction);
                    sq += v * v;
                }
                values[i] = Math.sqrt(sq);
            } else {
                values[i] = reduceIps(view, base, ips, nComp, comp, ipReduction);
            }
        }
        buckets.push({labels, values, sorted: ascending(labels)});
    }
    if (!buckets.length) return null;
    return {
        field: field.name_canonical,
        component: magnitude ? "magnitude" : (field.components[comp] ?? ""),
        text(n) {
            for (const b of buckets) {
                const v = lookup(b, n);
                if (v !== undefined) return valueLabelText(v);
            }
            return undefined;
        },
    };
}
