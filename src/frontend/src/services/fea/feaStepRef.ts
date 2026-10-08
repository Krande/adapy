// Which step a field read is for: a case the bake stored, or a load
// combination materialised on request.
//
// A stored step is an index into the base blob's step stack. A combination
// case lives in its own single-step blobs under the bake's case tree
// (``cases/<n>-<hash8>/``, same filenames as the base), so reading it is
// "the same field, its blob URLs moved under the case prefix, step 0". That
// substitution is what ``caseFieldView`` builds: every reader downstream (the
// AFBL / AFEL step fetchers, the paint kernels, the warp resolver) stays
// unaware of combinations and just reads step 0 of the view it is handed.

import type {
    FeaCaseField,
    FeaCaseOverlay,
    FeaManifest,
    FeaManifestField,
} from "../viewerApi";

export type FeaStepRef = {stored: number} | {case: number};

export function isCaseRef(ref: FeaStepRef | number): ref is {case: number} {
    return typeof ref === "object" && ref !== null && "case" in ref;
}

/** The index to read inside the blob a step lives in: the stored index for a
 *  stored step (a plain number is a stored index), 0 for a case's single-step
 *  blob. A case ref must be read through a ``caseFieldView``. */
export function blobStepIndex(ref: FeaStepRef | number): number {
    if (typeof ref === "number") return ref;
    return isCaseRef(ref) ? 0 : ref.stored;
}

/** The case's blob directory RELATIVE to the bake directory (``cases/101-ab12cd34/``),
 *  which is what a fetcher rooted at ``_derived/<src>.fea/`` resolves. Taken
 *  from the overlay's ``prefix`` when it sits under the bake directory, else
 *  built from the manifest's convention. */
export function caseRelativePrefix(
    overlay: Pick<FeaCaseOverlay, "prefix" | "case">,
    sourceKey?: string,
    manifest?: Pick<FeaManifest, "lazy_cases"> | null,
): string {
    const prefix = (overlay.prefix ?? "").replace(/^\/+/, "");
    if (sourceKey) {
        const base = `_derived/${sourceKey.replace(/^\/+/, "")}.fea/`;
        if (prefix.startsWith(base)) return prefix.slice(base.length);
    }
    const marker = prefix.indexOf(".fea/");
    if (marker >= 0) return prefix.slice(marker + ".fea/".length);
    const casesPrefix = manifest?.lazy_cases?.cases_prefix ?? "cases/";
    return `${casesPrefix}${overlay.case.n}-${overlay.case.recipe_hash.slice(0, 8)}/`;
}

function join(rel: string, url: string): string {
    return `${rel}${url.replace(/^\/+/, "")}`;
}

/** ``field`` as it reads for one materialised case: blob URLs under the case
 *  prefix, one step (the case), and the case's own range. Null when the
 *  overlay does not carry the field (a property field, or a field the case
 *  producer skipped) -- the caller then reads the base field. */
export function caseFieldView(
    field: FeaManifestField,
    overlay: FeaCaseOverlay,
    relPrefix: string,
): FeaManifestField | null {
    const cf: FeaCaseField | undefined = overlay.fields.find((f) => f.name_canonical === field.name_canonical);
    if (!cf) return null;
    const step = cf.steps?.[0] ?? {
        i: 0,
        value: overlay.case.n,
        label: String(overlay.case.n),
        ...(overlay.case.name ? {name: overlay.case.name} : {}),
    };
    const view: FeaManifestField = {
        ...field,
        n_steps: 1,
        steps: [{...step, i: 0}],
        scalar_range: cf.scalar_range ?? field.scalar_range,
    };
    if (field.blob) {
        const blob = cf.blob ?? field.blob;
        view.blob = {...field.blob, ...blob, url: join(relPrefix, blob.url)};
    }
    if (field.per_type) {
        view.per_type = field.per_type.map((bucket) => {
            const cb = cf.per_type?.find((p) => p.elem_type === bucket.elem_type);
            const blob = cb?.blob ?? bucket.blob;
            return {
                ...bucket,
                blob: {...bucket.blob, ...blob, url: join(relPrefix, blob.url)},
                scalar_range: cb?.scalar_range ?? bucket.scalar_range,
            };
        });
    }
    return view;
}

/** The manifest as it reads for one case: every field the overlay carries
 *  replaced by its case view, the rest (property fields) left as they are. */
export function caseManifestView(
    manifest: FeaManifest,
    overlay: FeaCaseOverlay,
    relPrefix: string,
): FeaManifest {
    return {
        ...manifest,
        fields: manifest.fields.map((f) => caseFieldView(f, overlay, relPrefix) ?? f),
    };
}
