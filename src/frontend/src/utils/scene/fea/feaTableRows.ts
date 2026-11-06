// The FEA result data table's rows: which nodes the table lists (the filter and the
// sort) and the values of the shown step. Shared by the table panel (reads them in
// its own window) and `core:fea-table` (`./feaTableMirror`, which reads them in the
// main window for a pop-out). Pure but for `loadFeaStepValues`.

import type {FeaManifestField} from "@/services/viewerApi";

/** Sorted by a column: 0 is the node id, 1.. the components, then (a vector field) the magnitude. */
export interface TableSort {
    col: number;
    dir: "asc" | "desc";
}

/**
 * The source rows the table lists, in order: those whose 1-based node id holds `filter`
 * as text, sorted by `sort` (null = natural order). `values` is the step's flat
 * (node x component) array.
 */
export function tableRowIndices(
    nPoints: number,
    filter: string,
    sort: TableSort | null,
    values: Float32Array | null,
    nComponents: number,
    isVector: boolean,
): Int32Array {
    if (nPoints === 0) return new Int32Array(0);
    let indices: Int32Array;
    const f = filter.trim();
    if (!f) {
        indices = new Int32Array(nPoints);
        for (let i = 0; i < nPoints; i++) indices[i] = i;
    } else {
        const buf: number[] = [];
        for (let i = 0; i < nPoints; i++) {
            if (String(i + 1).includes(f)) buf.push(i);
        }
        indices = Int32Array.from(buf);
    }
    if (!sort || !values) return indices;

    // Sort key per row: col 0 the node id, 1..n the component, n + 1 (a vector) the magnitude.
    // Materialised once, then an index array is sorted: no closure work inside the comparator.
    const {col, dir} = sort;
    const sign = dir === "asc" ? 1 : -1;
    const isMagCol = isVector && col === nComponents + 1;
    const compIdx = col - 1;
    const keys = new Float64Array(indices.length);
    for (let k = 0; k < indices.length; k++) {
        const row = indices[k];
        if (col === 0) {
            keys[k] = row + 1;
        } else if (isMagCol) {
            const off = row * nComponents;
            let m = 0;
            for (let c = 0; c < Math.min(nComponents, 3); c++) {
                const v = values[off + c] || 0;
                m += v * v;
            }
            keys[k] = Math.sqrt(m);
        } else {
            const v = values[row * nComponents + compIdx];
            keys[k] = isFinite(v) ? v : 0;
        }
    }
    const sortedOrder = Array.from(indices)
        .map((row, k) => ({row, key: keys[k]}))
        .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0) * sign)
        .map((x) => x.row);
    return Int32Array.from(sortedOrder);
}

/** Does the field list per-node values (a nodal field)? Element fields live on integration points. */
export function isNodalField(field: FeaManifestField | null | undefined): field is FeaManifestField {
    return !!field && !(field.per_type && field.per_type.length > 0);
}

/**
 * The values of step `stepIndex` of `field`: only that step is range-fetched, a load
 * combination reads its materialised case, and the result is cached per step.
 */
export async function loadFeaStepValues(scopeUrl: string, sourceName: string, field: FeaManifestField, stepIndex: number): Promise<Float32Array> {
    const [{fetchFieldStep}, {capabilities}, {fieldAtStep}, {slotRef}, {useFeaAnimationStore}] = await Promise.all([
        import("@/services/feaFieldBlob"),
        import("@/services/capabilities"),
        import("@/services/fea/feaCaseResolver"),
        import("@/utils/scene/fea/caseSteps"),
        import("@/state/feaAnimationStore"),
    ]);
    // The transport's fetchers for this source's bake: the whole-blob fetcher (fallback), the
    // range fetcher, and a stable cache key.
    const {fetcher, rangeFetcher, cacheKey} = capabilities.fea.fieldFetchers(scopeUrl, sourceName);
    const manifest = useFeaAnimationStore.getState().manifest;
    const ref = slotRef(manifest, field, stepIndex);
    const at = await (manifest
        ? fieldAtStep(manifest, field, ref, {scope: scopeUrl, sourceKey: sourceName})
        : Promise.resolve({field, step: stepIndex}));
    if (!at) throw new Error("this load combination does not carry the field");
    return fetchFieldStep(rangeFetcher, fetcher, at.field, at.step, cacheKey);
}
