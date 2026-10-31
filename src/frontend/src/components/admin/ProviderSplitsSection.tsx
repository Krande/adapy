import React, {useCallback, useEffect, useMemo, useState} from "react";

import {assetProviderCollections, type AssetProviderCollections} from "@/services/assetScopeCollections";
import {
    COLLECTION_SPLITS_KEY,
    type CollectionCopy,
    type CollectionSplitsMap,
    parseCollectionSplits,
    serialiseCollectionSplits,
    withSplit,
} from "@/services/collectionSplits";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {useProviderName} from "@/state/providerNamesStore";

import AdminSection from "./AdminSection";

// Admin → Providers → Split by location: offer one collection as several, one per copy.
//
// A provider that finds a collection in several places (one project on several file servers) reads
// the copy it prefers, and a run can pin another (the per-collection share choice). When the copies
// are really different datasets, split the collection: core then offers each ticked copy as a
// collection of its own -- its own published data, cursor, grants and schedules -- in every list it
// offers (sweeps, schedules, requests), and stops offering the original. See
// `ada.comms.rest.collection_splits`.
//
// Nothing moves when a collection is split: what was published under the original stays until an
// admin deletes it, and each copy is empty until it is exported.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));

const CopyLine: React.FC<{copy: CollectionCopy}> = ({copy}) => (
    <span className="text-gray-400">
        <span className="text-gray-100">{copy.label}</span>
        {copy.location && <> · {copy.location}</>}
        {copy.release && <> · {copy.release}</>}
        {copy.lastWritten && <> · last written {copy.lastWritten}</>}
        {copy.isDefault && <span className="ml-1 text-gray-500">(read when unsplit)</span>}
        {copy.root && <span className="ml-1 text-gray-600">{copy.root}</span>}
    </span>
);

const SplitRow: React.FC<{
    collection: string;
    copies: readonly CollectionCopy[];
    chosen: readonly string[];
    busy: boolean;
    onChange: (copies: string[]) => void;
}> = ({collection, copies, chosen, busy, onChange}) => {
    const split = chosen.length > 0;
    const isChosen = (id: string) => chosen.some((c) => c.toLowerCase() === id.toLowerCase());
    const toggle = (id: string) =>
        onChange(isChosen(id) ? chosen.filter((c) => c.toLowerCase() !== id.toLowerCase()) : [...chosen, id]);
    return (
        <tr className="border-t border-gray-800 align-top">
            <td className="px-2 py-1 font-mono text-gray-100">{collection}</td>
            <td className="px-2 py-1">
                <label className="inline-flex items-center gap-1">
                    <input
                        type="checkbox"
                        checked={split}
                        disabled={busy}
                        onChange={() => onChange(split ? [] : copies.map((c) => c.collection))}
                    />
                    split
                </label>
            </td>
            <td className="px-2 py-1">
                {split ? (
                    <div className="flex flex-col gap-0.5">
                        {copies.map((c) => (
                            <label key={c.collection} className="inline-flex items-start gap-1">
                                <input
                                    type="checkbox"
                                    className="mt-0.5"
                                    checked={isChosen(c.collection)}
                                    disabled={busy || (isChosen(c.collection) && chosen.length === 1)}
                                    title={isChosen(c.collection) && chosen.length === 1 ? "Untick 'split' to offer the collection unsplit" : undefined}
                                    onChange={() => toggle(c.collection)}
                                />
                                <CopyLine copy={c} />
                            </label>
                        ))}
                    </div>
                ) : (
                    <span className="text-gray-500">
                        {copies.length} copies; read from{" "}
                        {copies.find((c) => c.isDefault)?.label ?? "the provider's choice"}
                    </span>
                )}
            </td>
        </tr>
    );
};

const ProviderSplitsSection: React.FC = () => {
    const pn = useProviderName();
    const [providers, setProviders] = useState<AssetProviderCollections[]>([]);
    const [splits, setSplits] = useState<CollectionSplitsMap>({});
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const [plugins, raw] = await Promise.all([
                viewerApi.listBackendPlugins(),
                viewerApi.getPublicSetting(COLLECTION_SPLITS_KEY).catch(() => null),
            ]);
            setProviders(assetProviderCollections(plugins.plugins ?? []));
            setSplits(parseCollectionSplits(raw));
            setError(null);
        } catch (e) {
            setError(errText(e));
        }
    }, []);
    useEffect(() => {
        void load();
    }, [load]);

    const splittable = useMemo(() => providers.filter((p) => Object.keys(p.copies).length > 0), [providers]);

    const change = async (provider: string, collection: string, copies: string[]) => {
        setBusy(true);
        try {
            // Re-read before writing: another admin's split of another collection must survive this one.
            const fresh = parseCollectionSplits(await viewerApi.getPublicSetting(COLLECTION_SPLITS_KEY).catch(() => null));
            const next = withSplit(fresh, provider, collection, copies);
            await viewerApi.adminSetSetting(COLLECTION_SPLITS_KEY, serialiseCollectionSplits(next));
            setSplits(next);
            setError(null);
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    if (!splittable.length && !error) return null;
    return (
        <AdminSection
            title="Split by location"
            kind="Collections"
            storageKey="ada.admin.providers.open.splits"
            defaultOpen={false}
            testId="provider-splits-admin"
        >
            <div className="px-3 pt-2 pb-1 text-xs text-gray-400">
                A collection found in several places is read from one copy. Split it to offer each ticked copy as a
                collection of its own -- its own data, change cursor, grants and schedules. The unsplit collection's
                published data stays until you delete it; each copy is empty until it is exported.
            </div>
            {error && <div className="px-3 py-1 text-xs text-red-300">{error}</div>}
            {splittable.map((p) => (
                <div key={p.providerId} className="px-3 pb-2">
                    <div className="py-1 text-xs text-gray-300">{pn(p.providerId)}</div>
                    <table className="w-full text-xs">
                        <tbody>
                            {Object.entries(p.copies)
                                .sort(([a], [b]) => a.localeCompare(b))
                                .map(([collection, copies]) => (
                                    <SplitRow
                                        key={collection}
                                        collection={collection}
                                        copies={copies}
                                        chosen={splits[p.providerId]?.[collection.toLowerCase()] ?? []}
                                        busy={busy}
                                        onChange={(next) => void change(p.providerId, collection, next)}
                                    />
                                ))}
                        </tbody>
                    </table>
                </div>
            ))}
        </AdminSection>
    );
};

export default ProviderSplitsSection;
