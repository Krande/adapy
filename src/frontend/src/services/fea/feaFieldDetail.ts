// The per-model tables a split manifest leaves in sidecars (bake_version 6).
//
// An inline manifest carried, for every element field, each bucket's
// ``element_labels`` and ``element_node_indices`` -- one row per element,
// repeated per field -- plus every set's members and every node's solver id.
// On a large SIN model that was 148 MB of JSON parsed before the first pixel.
// The bake now writes the manifest as an index and these as sidecars beside it
// (see the backend's ``artefacts/manifest.py:split_manifest``):
//
//   fields[].detail.url     -> fea.detail.<sha16>.json  {per_type: [{elem_type, element_labels, element_node_indices}]}
//   groups_url              -> fea.groups.json          the sets, as ``groups`` was
//   mesh.node_labels_url    -> fea.nodelabels.json      the node ids, as ``mesh.node_labels`` was
//
// Everything here resolves to the inline shape, so the code that reads the
// tables (the element paint, the element readout, the Selected Object panel,
// the groups picker) runs unchanged on either layout: an inline manifest
// passes straight through without a fetch. Each sidecar is fetched once per
// source and cached (content-addressed detail files are shared by every field
// over the same mesh, so one fetch usually serves them all).

import type {FeaFetcher} from "./feaFetcher";
import type {FeaManifest, FeaManifestField, FeaManifestFieldPerType} from "../api/fea";

interface FieldDetailDoc {
    version?: number;
    per_type?: Array<Pick<FeaManifestFieldPerType, "elem_type" | "element_labels" | "element_node_indices">>;
}

/** Parsed sidecars, keyed ``<cacheKey>|<filename>``. Promises, so concurrent
 *  callers share one fetch; a failed fetch is evicted so a retry can succeed. */
const SIDECARS = new Map<string, Promise<unknown>>();

/** Drop every cached sidecar (a new bake of a source, tests). */
export function clearFeaSidecarCache(): void {
    SIDECARS.clear();
}

function sidecar<T>(fetcher: FeaFetcher, cacheKey: string, filename: string): Promise<T> {
    const key = `${cacheKey}|${filename}`;
    let hit = SIDECARS.get(key) as Promise<T> | undefined;
    if (!hit) {
        hit = fetcher(filename).then((buf) => JSON.parse(new TextDecoder().decode(buf)) as T);
        hit.catch(() => SIDECARS.delete(key));
        SIDECARS.set(key, hit);
    }
    return hit;
}

/** Whether ``field``'s per-element tables still have to be fetched. */
export function fieldNeedsDetail(field: FeaManifestField): boolean {
    return !!field.detail?.url && (field.per_type ?? []).some((b) => b.element_labels === undefined);
}

/** ``field`` with its buckets' ``element_labels`` / ``element_node_indices``
 *  filled in from its detail sidecar -- the field itself when it carries them
 *  inline (a pre-split bake) or has no element buckets. Works on a case view
 *  (``caseFieldView``) too: it keeps the base field's ``detail``, and a
 *  combination does not change which elements a bucket holds. */
export async function withFieldDetail(
    field: FeaManifestField,
    fetcher: FeaFetcher,
    cacheKey: string,
): Promise<FeaManifestField> {
    if (!fieldNeedsDetail(field)) return field;
    const doc = await sidecar<FieldDetailDoc>(fetcher, cacheKey, field.detail!.url);
    const rows = doc.per_type ?? [];
    return {
        ...field,
        per_type: (field.per_type ?? []).map((bucket, i) => {
            // Bucket order is the bake's; match by elem_type as a check on it.
            const d = rows[i]?.elem_type === bucket.elem_type ? rows[i] : rows.find((r) => r.elem_type === bucket.elem_type);
            if (!d) throw new Error(`field ${field.name_canonical}: no ${bucket.elem_type} tables in ${field.detail!.url}`);
            return {
                ...bucket,
                element_labels: d.element_labels ?? [],
                ...(d.element_node_indices ? {element_node_indices: d.element_node_indices} : {}),
            };
        }),
    };
}

/** The manifest's sets: inline ``groups``, else ``groups_url``'s sidecar. */
export async function manifestGroups(
    manifest: FeaManifest,
    fetcher: FeaFetcher,
    cacheKey: string,
): Promise<NonNullable<FeaManifest["groups"]>> {
    if (manifest.groups) return manifest.groups;
    if (!manifest.groups_url) return [];
    return sidecar<NonNullable<FeaManifest["groups"]>>(fetcher, cacheKey, manifest.groups_url);
}

/** The element's type bucket and its node numbers, from the first element
 *  field that lists it (property fields first: every element of the model is
 *  in them, and they are few). Node numbers are the solver's ids when the bake
 *  has them, else point indices. Null when no element field lists it. */
export async function elementNodes(
    manifest: FeaManifest,
    label: number,
    fetcher: FeaFetcher,
    cacheKey: string,
): Promise<{elemType: string; nodes: number[]} | null> {
    const fields = [...(manifest.fields ?? [])]
        .filter((f) => f.per_type?.length)
        .sort((a, b) => Number(b.category === "property") - Number(a.category === "property"));
    for (const base of fields) {
        const field = await withFieldDetail(base, fetcher, cacheKey);
        for (const bucket of field.per_type ?? []) {
            const i = bucket.element_labels?.indexOf(label) ?? -1;
            if (i < 0) continue;
            const idx = bucket.element_node_indices?.[i] ?? [];
            const nodeLabels = await manifestNodeLabels(manifest, fetcher, cacheKey);
            return {elemType: bucket.elem_type, nodes: idx.map((n) => nodeLabels?.[n] ?? n)};
        }
    }
    return null;
}

/** The solver node ids aligned to the points array, or null when the bake has none. */
export async function manifestNodeLabels(
    manifest: FeaManifest,
    fetcher: FeaFetcher,
    cacheKey: string,
): Promise<number[] | null> {
    const mesh = manifest.mesh;
    if (mesh?.node_labels) return mesh.node_labels;
    if (!mesh?.node_labels_url) return null;
    return sidecar<number[]>(fetcher, cacheKey, mesh.node_labels_url);
}
