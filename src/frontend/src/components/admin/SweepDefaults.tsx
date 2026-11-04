import React, {useEffect, useMemo, useState} from "react";

import {ApiError, viewerApi} from "@/services/viewerApi";
import type {CollectionSettings, OverridableSetting} from "@/services/viewerApi";
import {sortProjects} from "@/utils/sortProjects";

// Admin → Providers → a provider's DEFAULT sweep list: which collections every new sweep of it --
// scheduled or one-off -- starts with ticked off. Some collections are too big for a routine sweep;
// unticking them once here beats remembering to untick them in every run. A running sweep's own
// "Run" boxes still decide for that sweep (`ProviderSweepRuns`).
//
// And PER-COLLECTION SETTINGS: a collection's own value of one of the sweep's settings (the export
// timeout of a project too large for the sweep-wide one). It wins over the run's choice for that
// collection, in every sweep, from the next collection started.

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

/** `per` with `collection`'s `name` set to `value` -- or cleared, for `undefined` -- and a
 *  collection left with no settings dropped. A new object; `per` is not changed. */
export function withCollectionSetting(
    per: CollectionSettings,
    collection: string,
    name: string,
    value: unknown,
): CollectionSettings {
    const next: CollectionSettings = {...per};
    const own = {...(next[collection] ?? {})};
    if (value === undefined) delete own[name];
    else own[name] = value;
    if (Object.keys(own).length) next[collection] = own;
    else delete next[collection];
    return next;
}

/** Whether two per-collection settings say the same (order-blind). */
export function sameCollectionSettings(a: CollectionSettings, b: CollectionSettings): boolean {
    const norm = (p: CollectionSettings) =>
        JSON.stringify(
            Object.keys(p)
                .sort()
                .map((c) => [c, Object.keys(p[c]).sort().map((k) => [k, p[c][k]])]),
        );
    return norm(a) === norm(b);
}

const SweepDefaults: React.FC<{provider: string}> = ({provider}) => {
    const [open, setOpen] = useState(false);
    const [collections, setCollections] = useState<string[]>([]);
    const [saved, setSaved] = useState<string[]>([]);
    const [excluded, setExcluded] = useState<string[]>([]);
    const [overridable, setOverridable] = useState<OverridableSetting[]>([]);
    const [savedPer, setSavedPer] = useState<CollectionSettings>({});
    const [per, setPer] = useState<CollectionSettings>({});
    const [adding, setAdding] = useState("");
    const [filter, setFilter] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let alive = true;
        viewerApi
            .adminAssetSweepDefaults(provider)
            .then((d) => {
                if (!alive) return;
                setCollections(sortProjects(d.collections ?? []));
                setSaved(d.excluded);
                setExcluded(d.excluded);
                setOverridable(d.overridable ?? []);
                setSavedPer(d.collection_settings ?? {});
                setPer(d.collection_settings ?? {});
                setError(null);
            })
            .catch((e) => alive && setError(errText(e)));
        return () => {
            alive = false;
        };
    }, [provider]);

    const notRun = useMemo(() => sortProjects(saved), [saved]);
    const perDirty = useMemo(() => !sameCollectionSettings(savedPer, per), [savedPer, per]);
    const dirty = useMemo(() => saved.join("\n") !== excluded.join("\n") || perDirty, [saved, excluded, perDirty]);
    const withOwn = useMemo(() => sortProjects(Object.keys(per)), [per]);
    const shown = useMemo(() => {
        const f = filter.trim().toLowerCase();
        return f ? collections.filter((c) => c.toLowerCase().includes(f)) : collections;
    }, [collections, filter]);

    const save = async () => {
        setBusy(true);
        try {
            // Only send the per-collection settings when they changed: a page from before them
            // (or one whose provider is offline, so nothing is overridable) must not clear them.
            const d = await viewerApi.adminSetAssetSweepDefaults(provider, excluded, perDirty ? per : undefined);
            setSaved(d.excluded);
            setExcluded(d.excluded);
            setSavedPer(d.collection_settings ?? {});
            setPer(d.collection_settings ?? {});
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
                {notRun.length ? ` · ${notRun.length} not run (${notRun.slice(0, 4).join(", ")}${notRun.length > 4 ? ", …" : ""})` : " · all run"}
                {Object.keys(savedPer).length ? ` · ${Object.keys(savedPer).length} with own settings` : ""}
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
                            <button type="button" className="border border-gray-700 px-2 py-0.5 rounded-sm" onClick={() => {
                                setExcluded(saved);
                                setPer(savedPer);
                            }}>
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
                    {overridable.length > 0 && (
                        <div className="border-t border-gray-800 pt-1.5 space-y-1" data-testid="sweep-defaults-own-settings">
                            <div className="text-[11px] text-gray-500">
                                Own settings per collection: used for that collection in every sweep instead of the
                                sweep's choice ({overridable.map((s) => s.title).join(", ")}).
                            </div>
                            {withOwn.map((c) => (
                                <div key={c} className="flex items-center gap-2">
                                    <span className="font-mono w-28 truncate" title={c}>
                                        {c}
                                    </span>
                                    {overridable.map((s) => {
                                        const idx = s.choices.findIndex((ch) => String(ch.value) === String(per[c]?.[s.name]));
                                        return (
                                            <label key={s.name} className="inline-flex items-center gap-1" title={s.description ?? undefined}>
                                                <span className="text-gray-400">{s.title}</span>
                                                <select
                                                    className="bg-gray-800 border border-gray-700 rounded-sm px-1 py-0.5"
                                                    value={idx < 0 ? "" : String(idx)}
                                                    onChange={(e) =>
                                                        setPer(
                                                            withCollectionSetting(
                                                                per,
                                                                c,
                                                                s.name,
                                                                e.target.value === "" ? undefined : s.choices[Number(e.target.value)].value,
                                                            ),
                                                        )
                                                    }
                                                >
                                                    <option value="">the sweep's</option>
                                                    {s.choices.map((ch, k) => (
                                                        <option key={k} value={String(k)}>
                                                            {ch.label}
                                                        </option>
                                                    ))}
                                                </select>
                                            </label>
                                        );
                                    })}
                                    <button
                                        type="button"
                                        className="text-gray-500 hover:text-gray-200"
                                        title="Remove this collection's own settings"
                                        onClick={() => {
                                            const next = {...per};
                                            delete next[c];
                                            setPer(next);
                                        }}
                                    >
                                        ✕
                                    </button>
                                </div>
                            ))}
                            <div className="flex items-center gap-2">
                                <select
                                    className="bg-gray-800 border border-gray-700 rounded-sm px-1 py-0.5 font-mono"
                                    value={adding}
                                    onChange={(e) => setAdding(e.target.value)}
                                >
                                    <option value="">Add a collection…</option>
                                    {collections
                                        .filter((c) => !(c in per))
                                        .map((c) => (
                                            <option key={c} value={c}>
                                                {c}
                                            </option>
                                        ))}
                                </select>
                                <button
                                    type="button"
                                    className="border border-gray-700 hover:bg-gray-800 disabled:opacity-50 px-2 py-0.5 rounded-sm"
                                    disabled={!adding}
                                    onClick={() => {
                                        // Starts on the first setting's first choice: a row with nothing set is
                                        // not a setting, and would vanish on save.
                                        const s = overridable[0];
                                        setPer(withCollectionSetting(per, adding, s.name, s.choices[0]?.value));
                                        setAdding("");
                                    }}
                                >
                                    Add
                                </button>
                            </div>
                        </div>
                    )}
                    {error && <div className="text-red-300">{error}</div>}
                </div>
            )}
        </div>
    );
};

export default SweepDefaults;
