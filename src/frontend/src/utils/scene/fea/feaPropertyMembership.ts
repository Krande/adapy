// Which elements carry which property value, read when first asked for.
//
// The manifest names a property's values (a material's name, a plate thickness)
// but not which element has which: that is in the field's per-element blobs, the
// same ones the property colouring reads. They are fetched once per property
// field and source, the first time the Scene tree's FEA rows or the Selected
// Object panel need them, through the fetchers the colouring uses (shared caches,
// so a property already painted costs nothing). A result swapped underneath a
// read drops it: its element ids would name the wrong model.
//
// A property field holds one value per element: one step, one integration point,
// one component. A bucket with more slots per element is skipped and said out
// loud rather than read wrongly.

import {capabilities} from "@/services/capabilities";
import type {FeaManifestField} from "@/services/api/fea";
import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {withFieldDetail} from "@/services/fea/feaFieldDetail";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useFeaIsolationStore} from "@/state/feaIsolationStore";

import type {MembershipStatus, PropertyMembership} from "./feaTreeRows";
import {feaSourceScope} from "./streaming/session";

/** One field's element -> value map, from its buckets. */
async function readMembership(field: FeaManifestField, sourceName: string): Promise<PropertyMembership> {
    const {fetcher, rangeFetcher, cacheKey} = capabilities.fea.fieldFetchers(feaSourceScope(sourceName), sourceName);
    // A split manifest keeps the element labels in the field's detail sidecar.
    const full = await withFieldDetail(field, fetcher, cacheKey);
    const out = new Map<string, number>();
    for (const bucket of full.per_type ?? []) {
        const slots = (bucket.n_ips || 1) * Math.max(full.components.length, 1);
        if (slots !== 1) {
            console.warn(`[fea] property ${full.name_canonical}: bucket ${bucket.elem_type} has ${slots} values per element, expected 1 -- skipped`);
            continue;
        }
        const values = await fetchElemFieldStep(rangeFetcher, fetcher, bucket, 0, cacheKey);
        const labels = bucket.element_labels ?? [];
        const n = Math.min(bucket.n_elements, labels.length, values.length);
        for (let e = 0; e < n; e++) {
            const v = values[e];
            if (Number.isFinite(v)) out.set(`E${labels[e]}`, v);
        }
    }
    return out;
}

function setStatus(name: string, status: MembershipStatus | null): void {
    const s = useFeaIsolationStore.getState();
    const next = new Map(s.membershipStatus);
    if (status) next.set(name, status);
    else next.delete(name);
    useFeaIsolationStore.setState({membershipStatus: next});
}

/**
 * Make sure `field`'s membership is read or being read. Idempotent and cheap on a hit; a
 * miss starts one read, published when it lands. A field whose read failed is not retried
 * until the result changes.
 */
export function ensurePropertyMembership(field: FeaManifestField): void {
    const {sourceName} = useFeaAnimationStore.getState();
    if (!sourceName) return;
    const store = useFeaIsolationStore.getState();
    if (store.source !== sourceName) store.reset(sourceName);
    const name = field.name_canonical;
    const now = useFeaIsolationStore.getState();
    if (now.membership.has(name) || now.membershipStatus.has(name)) return;
    setStatus(name, "loading");
    void readMembership(field, sourceName)
        .then((membership) => {
            if (useFeaIsolationStore.getState().source !== sourceName) return;
            const byField = new Map(useFeaIsolationStore.getState().membership);
            byField.set(name, membership);
            useFeaIsolationStore.setState({membership: byField});
            setStatus(name, null);
        })
        .catch((err) => {
            if (useFeaIsolationStore.getState().source !== sourceName) return;
            console.warn(`[fea] which elements carry ${name} could not be read:`, err);
            setStatus(name, "failed");
        });
}

/** `ensurePropertyMembership` for every field given. */
export function ensurePropertyMemberships(fields: readonly FeaManifestField[]): void {
    for (const f of fields) ensurePropertyMembership(f);
}
