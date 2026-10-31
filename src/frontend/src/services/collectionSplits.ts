// Split collections: one provider collection offered as several, one per source copy.
//
// The viewer's half of `ada.comms.rest.collection_splits` (read that docstring first). A provider
// declares, in the spec field named by `asset_collection_splits_field`, what each collection would
// split into:
//
//   copies = { "ABC": [{ collection: "ABC-X", label: "ABC (X)", location: "...", ... }, ...] }
//
// and the admin's choice is core's public setting `public.assets.collection_splits`:
//
//   { provider: { collection: [copy id, ...] } }
//
// A split collection is offered as its chosen copies, never itself; a split whose copies the
// provider no longer declares falls back to the collection. The server applies the same rule to
// every list it offers (sweeps, schedules, requests), so the two cannot disagree.

export const COLLECTION_SPLITS_KEY = "public.assets.collection_splits";

/** One copy a provider declares for a collection. */
export interface CollectionCopy {
    collection: string;
    label: string;
    location: string | null;
    root: string | null;
    release: string | null;
    lastWritten: string | null;
    /** The copy the provider reads the unsplit collection from. */
    isDefault: boolean;
}

/** `{provider: {collection (lower case): [copy id]}}`. */
export type CollectionSplitsMap = Record<string, Record<string, string[]>>;

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);

/** The setting's stored text, read leniently: anything malformed is no split. */
export function parseCollectionSplits(raw: string | null | undefined): CollectionSplitsMap {
    let doc: unknown;
    try {
        doc = raw ? JSON.parse(raw) : {};
    } catch {
        return {};
    }
    const out: CollectionSplitsMap = {};
    if (!isRecord(doc)) return out;
    for (const [provider, splits] of Object.entries(doc)) {
        if (!isRecord(splits)) continue;
        const kept: Record<string, string[]> = {};
        for (const [collection, copies] of Object.entries(splits)) {
            if (!Array.isArray(copies)) continue;
            const chosen = copies.map(str).filter((c): c is string => c !== null);
            if (chosen.length) kept[collection.trim().toLowerCase()] = chosen;
        }
        if (Object.keys(kept).length) out[provider] = kept;
    }
    return out;
}

export function serialiseCollectionSplits(map: CollectionSplitsMap): string {
    return JSON.stringify(map);
}

/** `{collection: [copy]}` as a plugin spec declares it; `{}` when it declares none. */
export function declaredCopies(spec: unknown): Record<string, CollectionCopy[]> {
    if (!isRecord(spec)) return {};
    const field = spec.asset_collection_splits_field;
    const raw = typeof field === "string" ? spec[field] : undefined;
    const out: Record<string, CollectionCopy[]> = {};
    if (!isRecord(raw)) return out;
    for (const [collection, entries] of Object.entries(raw)) {
        if (!Array.isArray(entries)) continue;
        const copies: CollectionCopy[] = [];
        for (const e of entries) {
            if (!isRecord(e)) continue;
            const id = str(e.collection);
            if (!id) continue;
            copies.push({
                collection: id,
                label: str(e.label) ?? id,
                location: str(e.location),
                root: str(e.root),
                release: str(e.release),
                lastWritten: str(e.last_written),
                isDefault: e.default === true,
            });
        }
        if (copies.length) out[collection] = copies;
    }
    return out;
}

/** `collections` with each split one replaced by its chosen copies that `declared` still holds. */
export function applySplits(
    collections: readonly string[],
    declared: Readonly<Record<string, readonly CollectionCopy[]>>,
    splits: Readonly<Record<string, readonly string[]>> | undefined,
): string[] {
    if (!splits || !Object.keys(splits).length) return [...collections];
    const byLower = new Map(Object.entries(declared).map(([c, copies]) => [c.toLowerCase(), copies]));
    const out: string[] = [];
    for (const collection of collections) {
        const chosen = splits[collection.toLowerCase()] ?? [];
        const offered = new Map((byLower.get(collection.toLowerCase()) ?? []).map((c) => [c.collection.toLowerCase(), c.collection]));
        const copies = chosen.map((c) => offered.get(c.toLowerCase())).filter((c): c is string => !!c);
        out.push(...(copies.length ? copies : [collection]));
    }
    return [...new Set(out)];
}

/** `map` with `provider`'s split of `collection` set to `copies` (none: not split). */
export function withSplit(map: CollectionSplitsMap, provider: string, collection: string, copies: readonly string[]): CollectionSplitsMap {
    const next: CollectionSplitsMap = {...map, [provider]: {...(map[provider] ?? {})}};
    const key = collection.trim().toLowerCase();
    if (copies.length) next[provider][key] = [...copies];
    else delete next[provider][key];
    if (!Object.keys(next[provider]).length) delete next[provider];
    return next;
}
