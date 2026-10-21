import React, {useEffect, useMemo, useState} from "react";

import {ApiError, viewerApi} from "@/services/viewerApi";

// Admin → Providers → a provider's DEFAULT sweep list: which collections every new sweep of it --
// scheduled or one-off -- starts with ticked off. Some collections are too big for a routine sweep;
// unticking them once here beats remembering to untick them in every run. A running sweep's own
// "Run" boxes still decide for that sweep (`ProviderSweepRuns`).

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));

/** The excluded list after toggling `collection`, sorted like the server stores it. */
export function toggledExcluded(excluded: readonly string[], collection: string, run: boolean): string[] {
    const next = new Set(excluded);
    if (run) next.delete(collection);
    else next.add(collection);
    return [...next].sort();
}

/** The excluded list with every one of `collections` set to run (or not run) together. */
export function excludedWithAll(excluded: readonly string[], collections: readonly string[], run: boolean): string[] {
    const next = new Set(excluded);
    for (const c of collections) {
        if (run) next.delete(c);
        else next.add(c);
    }
    return [...next].sort();
}

const SweepDefaults: React.FC<{provider: string}> = ({provider}) => {
    const [open, setOpen] = useState(false);
    const [collections, setCollections] = useState<string[]>([]);
    const [saved, setSaved] = useState<string[]>([]);
    const [excluded, setExcluded] = useState<string[]>([]);
    const [filter, setFilter] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let alive = true;
        viewerApi
            .adminAssetSweepDefaults(provider)
            .then((d) => {
                if (!alive) return;
                setCollections(d.collections ?? []);
                setSaved(d.excluded);
                setExcluded(d.excluded);
                setError(null);
            })
            .catch((e) => alive && setError(errText(e)));
        return () => {
            alive = false;
        };
    }, [provider]);

    const dirty = useMemo(() => saved.join("\n") !== excluded.join("\n"), [saved, excluded]);
    const shown = useMemo(() => {
        const f = filter.trim().toLowerCase();
        return f ? collections.filter((c) => c.toLowerCase().includes(f)) : collections;
    }, [collections, filter]);

    const save = async () => {
        setBusy(true);
        try {
            const d = await viewerApi.adminSetAssetSweepDefaults(provider, excluded);
            setSaved(d.excluded);
            setExcluded(d.excluded);
            setError(null);
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="text-xs space-y-1" data-testid="sweep-defaults">
            <button type="button" className="text-[11px] text-gray-400 hover:text-gray-200" onClick={() => setOpen(!open)}>
                {open ? "▾" : "▸"} Default projects for new sweeps
                {saved.length ? ` · ${saved.length} not run (${saved.slice(0, 4).join(", ")}${saved.length > 4 ? ", …" : ""})` : " · all run"}
            </button>
            {open && (
                <div className="rounded-sm border border-gray-800 p-2 space-y-1.5">
                    <div className="flex items-center gap-2">
                        <input
                            className="bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5 text-xs flex-1"
                            placeholder="Filter collections"
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                        />
                        <button
                            type="button"
                            className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-2 py-0.5 rounded-sm"
                            disabled={!dirty || busy}
                            onClick={() => void save()}
                        >
                            {busy ? "Saving…" : "Save"}
                        </button>
                        {dirty && (
                            <button type="button" className="border border-gray-700 px-2 py-0.5 rounded-sm" onClick={() => setExcluded(saved)}>
                                Revert
                            </button>
                        )}
                    </div>
                    <div className="text-[11px] text-gray-500">
                        Ticked collections run in every new sweep; unticked ones start as "not run" and can still be ticked in a
                        running sweep.
                    </div>
                    {shown.length > 0 && (
                        // Act on what the filter shows: all of it unfiltered.
                        <div className="flex gap-2">
                            <button
                                type="button"
                                className="border border-gray-700 hover:bg-gray-800 disabled:opacity-50 px-2 py-0.5 rounded-sm"
                                disabled={shown.every((c) => !excluded.includes(c))}
                                onClick={() => setExcluded(excludedWithAll(excluded, shown, true))}
                            >
                                Select all{filter.trim() ? " shown" : ""}
                            </button>
                            <button
                                type="button"
                                className="border border-gray-700 hover:bg-gray-800 disabled:opacity-50 px-2 py-0.5 rounded-sm"
                                disabled={shown.every((c) => excluded.includes(c))}
                                onClick={() => setExcluded(excludedWithAll(excluded, shown, false))}
                            >
                                Deselect all{filter.trim() ? " shown" : ""}
                            </button>
                        </div>
                    )}
                    <div className="grid grid-cols-[repeat(auto-fill,minmax(7rem,1fr))] gap-x-2 gap-y-0.5 max-h-56 overflow-y-auto">
                        {shown.map((c) => (
                            <label key={c} className="inline-flex items-center gap-1 font-mono">
                                <input
                                    type="checkbox"
                                    checked={!excluded.includes(c)}
                                    onChange={(e) => setExcluded(toggledExcluded(excluded, c, e.target.checked))}
                                />
                                {c}
                            </label>
                        ))}
                    </div>
                    {error && <div className="text-red-300">{error}</div>}
                </div>
            )}
        </div>
    );
};

export default SweepDefaults;
