import React, {useCallback, useEffect, useMemo, useState} from "react";

import {AdminProject, viewerApi} from "@/services/viewerApi";
import {
    ASSET_SCOPE_COLLECTIONS_KEY,
    AssetCollectionsRefresh,
    AssetProviderCollections,
    EnabledCollections,
    ScopeCollectionsMap,
    assetProviderCollections,
    collectionChoices,
    STALE_PERSONAL_KEY,
    enabledFor,
    isPersonalKey,
    parseScopeCollections,
    personalKey,
    serialiseScopeCollections,
    storedProviders,
    toggleCollection,
    withEnabled,
} from "@/services/assetScopeCollections";
import {fuzzyFilter} from "@/services/fuzzy";
import {
    MAX_PROVIDER_LABEL_LENGTH,
    PROVIDER_LABELS_KEY,
    ProviderAliasesMap,
    cleanProviderLabel,
    labelProblem,
    parseProviderAliases,
    serialiseProviderAliases,
    withProviderAlias,
} from "@/assets/providerNames";
import {useProviderNamesStore} from "@/state/providerNamesStore";
import {useConversionStore} from "@/state/conversionStore";
import {useMeStore} from "@/state/meStore";
import {rescanStoreKey, startProviderRescan} from "@/services/providerRescan";

import AdminSection from "./AdminSection";
import ClashSpecProvidersSection from "./ClashSpecProvidersSection";
import ProviderSchedulesSection from "./ProviderSchedulesSection";

// Admin tab — which of an asset provider's collections may be REQUESTED in each
// scope.
//
// WHERE THE LIST COMES FROM. Not from core's asset-provider registry: a
// provider's collections exist only on the machine that can reach them, so a
// registry in the API process cannot answer. A backend plugin spec instead
// DECLARES the link -- `asset_provider_id` plus `asset_collections_field`, the
// name of the spec field listing the collections -- and this tab reads that off
// `GET /plugins`. Core names no provider here and needs no protocol for it.
//
// This replaced the External Models tab's per-scope binding (one collection a
// scope showed); that tab is gone.
//
// WHAT IT DOES NOT DO, said in the tab too: it narrows a provider's request
// picker. It is not a permission -- nothing on the server refuses a request for
// a collection left unticked -- and it hides nothing already published.
//
// OFFLINE IS NOT REVOCATION. A grant is the admin's intent; an advertisement is
// only what is reachable right now. Every stored grant is listed, whether or not
// a worker advertises it, and marked when none does -- so an admin opening this
// while a host reboots does not revoke that host's collections by saving.
//
// YOUR PERSONAL SCOPE IS ONE ROW, keyed `user:<your sub>`: an admin can tick collections for their
// OWN personal scope, and nobody else's is listed or touched by it. A personal scope offers NOTHING
// until something is ticked there -- unlike Shared and projects, where nothing configured is
// everything. A `user:me` key (an earlier version keyed every user's personal scope at once by it)
// is never read; it is listed as stale so it can be cleared.

interface ScopeRow {
    scope: string;
    label: string;
    hint: string;
    /** In the stored map but not a current scope: an archived or deleted
     *  project, or a hand-written key. Listed so it can be cleared. */
    stale?: boolean;
}

interface ProviderSection {
    providerId: string;
    /** Null when no online worker declares the provider right now. */
    live: AssetProviderCollections | null;
}

const rowKey = (scope: string, provider: string) => `${provider}\u0000${scope}`;

/** One scope's display name for one provider: saved on Enter or leaving the field, Escape puts it
 *  back. Empty clears the alias, and the provider's own label (the placeholder) is shown again. */
const AliasField: React.FC<{
    value: string;
    fallback: string;
    disabled: boolean;
    label: string;
    onSave: (next: string) => Promise<void>;
}> = ({value, fallback, disabled, label, onSave}) => {
    const [draft, setDraft] = useState(value);
    useEffect(() => setDraft(value), [value]);
    const problem = labelProblem(draft);
    const save = () => {
        const next = cleanProviderLabel(draft) ?? "";
        if (problem || next === value) {
            if (!problem) setDraft(value);
            return;
        }
        void onSave(next);
    };
    return (
        <div className="space-y-0.5">
            <input
                type="text"
                value={draft}
                disabled={disabled}
                placeholder={fallback}
                maxLength={MAX_PROVIDER_LABEL_LENGTH + 16}
                aria-label={label}
                title="What this scope's viewers see for the provider. Empty: the provider's own name. The provider id itself never changes."
                className={`w-44 rounded-sm border bg-gray-800 px-2 py-1 text-xs text-gray-100 placeholder:text-gray-500 ${
                    problem ? "border-red-500" : "border-gray-700"
                }`}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={save}
                onKeyDown={(e) => {
                    if (e.key === "Enter") e.currentTarget.blur();
                    else if (e.key === "Escape") {
                        setDraft(value);
                        // Blurring after the reset saves nothing: the draft is the stored value again.
                        requestAnimationFrame(() => (e.target as HTMLInputElement).blur());
                    }
                }}
            />
            {problem && <div className="text-[10px] text-red-300">{problem}</div>}
        </div>
    );
};

/** Where one provider's rescan is: running, or what it said when it finished. */
interface RescanState {
    running: boolean;
    note: string;
    failed?: boolean;
}

const SHOW_ENABLED_KEY = "ada.admin.providers.showEnabled";

/** A rescan's state as the tab shows it, from its toast entry (`services/providerRescan.ts`): the
 *  job's progress and stage while it runs, the provider's sentence when it is done. */
function rescanStateOf(entry: {status: string; progress: number; stage: string; error: string | null} | undefined): RescanState | null {
    if (!entry) return null;
    if (entry.status === "queued" || entry.status === "running") {
        const pct = Math.round(Math.min(1, Math.max(0, entry.progress || 0)) * 100);
        return {running: true, note: `Rescanning… ${pct}%${entry.stage ? ` · ${entry.stage}` : ""}`};
    }
    if (entry.status === "done") return {running: false, note: entry.stage || "Done."};
    return {running: false, note: entry.error || `the rescan ${entry.status}`, failed: true};
}

const ProvidersTab: React.FC = () => {
    const [live, setLive] = useState<AssetProviderCollections[]>([]);
    const [projects, setProjects] = useState<AdminProject[]>([]);
    const [map, setMap] = useState<ScopeCollectionsMap>({});
    const [aliases, setAliases] = useState<ProviderAliasesMap>({});
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState<string | null>(null);
    const [open, setOpen] = useState<string | null>(null);
    const [filter, setFilter] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [rescans, setRescans] = useState<Record<string, RescanState>>({});
    // The signed-in admin: their own personal scope is the one personal row.
    const mySub = useMeStore((s) => s.sub);
    const myName = useMeStore((s) => s.displayName || s.email);
    // A viewer convenience, so per browser: whether each restricted row lists its enabled
    // collections as tags beside the count.
    const [showEnabled, setShowEnabled] = useState<boolean>(() => {
        try {
            return localStorage.getItem(SHOW_ENABLED_KEY) !== "0";
        } catch {
            return true;
        }
    });
    const toggleShowEnabled = (on: boolean) => {
        setShowEnabled(on);
        try {
            localStorage.setItem(SHOW_ENABLED_KEY, on ? "1" : "0");
        } catch {
            // Not remembered; still applied for this visit.
        }
    };

    // A rescan runs in the global toast (`startProviderRescan`), not here: leaving the tab, or
    // reloading, does not stop it. The tab only shows its entry -- and `rescans` what went wrong
    // before there was one (the enqueue refused).
    const toastJobs = useConversionStore((s) => s.jobs);
    const rescanOf = (providerId: string): RescanState | null =>
        rescanStateOf(toastJobs[rescanStoreKey(providerId)]) ?? rescans[providerId] ?? null;
    const rescan = useCallback(async (providerId: string, refresh: AssetCollectionsRefresh, before: readonly string[], label: string) => {
        setRescans((prev) => ({...prev, [providerId]: {running: true, note: "Asking the worker…"}}));
        try {
            await startProviderRescan(providerId, refresh, before, label);
            setRescans((prev) => {
                const next = {...prev};
                delete next[providerId];
                return next;
            });
        } catch (e) {
            setRescans((prev) => ({...prev, [providerId]: {running: false, note: e instanceof Error ? e.message : String(e), failed: true}}));
        }
    }, []);

    const refresh = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [plugins, projs, raw, rawAliases] = await Promise.all([
                viewerApi.listBackendPlugins().catch(() => ({plugins: []})),
                viewerApi.adminListProjects().catch(() => [] as AdminProject[]),
                viewerApi.getPublicSetting(ASSET_SCOPE_COLLECTIONS_KEY).catch(() => null),
                viewerApi.getPublicSetting(PROVIDER_LABELS_KEY).catch(() => null),
            ]);
            setLive(assetProviderCollections(plugins.plugins ?? []));
            setProjects(projs.filter((p) => !p.archived_at));
            setMap(parseScopeCollections(raw));
            setAliases(parseProviderAliases(rawAliases));
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    // A rescan that finishes while the tab is open -- its finisher saw the new list, or waited it
    // out -- shows the advertised collections at once.
    const finishedRescans = Object.entries(toastJobs)
        .filter(([key, j]) => key.startsWith(rescanStoreKey("")) && j.status === "done")
        .map(([key]) => key)
        .sort()
        .join("\u0000");
    useEffect(() => {
        if (!finishedRescans) return;
        void viewerApi
            .listBackendPlugins()
            .then((plugins) => setLive(assetProviderCollections(plugins.plugins ?? [])))
            .catch(() => {});
    }, [finishedRescans]);

    // Live providers, plus any the stored maps name that no worker declares now.
    const sections: ProviderSection[] = useMemo(() => {
        const byId = new Map(live.map((p) => [p.providerId, p]));
        const aliased = Object.values(aliases).flatMap((row) => Object.keys(row));
        const ids = new Set([...byId.keys(), ...storedProviders(map), ...aliased]);
        return [...ids].sort().map((providerId) => ({providerId, live: byId.get(providerId) ?? null}));
    }, [live, map, aliases]);

    /** Store one scope's display name for one provider (empty clears it), applied to the STORED
     *  value as `persist` does, so another admin's edit since this tab loaded is kept. The server
     *  validates it again; its refusal is shown as the tab's error. */
    const persistAlias = useCallback(async (scope: string, provider: string, label: string) => {
        const key = rowKey(scope, provider);
        setBusy(key);
        setError(null);
        try {
            const latest = parseProviderAliases(await viewerApi.getPublicSetting(PROVIDER_LABELS_KEY).catch(() => null));
            const next = withProviderAlias(latest, scope, provider, label);
            await viewerApi.adminSetSetting(PROVIDER_LABELS_KEY, serialiseProviderAliases(next));
            setAliases(next);
            // A viewer of that scope in THIS browser sees the new name without a reload.
            // Your own personal row is keyed `user:<sub>`; the API names that scope `user:me`.
            void useProviderNamesStore.getState().load(isPersonalKey(scope) ? "user:me" : scope, true);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(null);
        }
    }, []);

    const rows: ScopeRow[] = useMemo(() => {
        const out: ScopeRow[] = [{scope: "shared", label: "Shared", hint: "everyone with access to this viewer"}];
        if (mySub) {
            out.push({
                scope: personalKey(mySub),
                label: "Your personal scope",
                hint: `${myName || mySub} only -- nothing is offered here until you tick it`,
            });
        }
        for (const p of projects) out.push({scope: `project:${p.id}`, label: p.name, hint: p.slug});
        const known = new Set(out.map((r) => r.scope));
        for (const scope of Object.keys(map).sort()) {
            // Another user's personal scope is theirs (another admin ticking their own): not listed here.
            if (known.has(scope) || isPersonalKey(scope)) continue;
            out.push({
                scope,
                label: scope,
                hint:
                    scope === STALE_PERSONAL_KEY
                        ? "applied to every personal scope by an earlier version; never read now -- clear it"
                        : "not a current project",
                stale: true,
            });
        }
        // A display name stored under the stale key is listed too, so it can be cleared.
        if (aliases[STALE_PERSONAL_KEY] && !out.some((r) => r.scope === STALE_PERSONAL_KEY)) {
            out.push({scope: STALE_PERSONAL_KEY, label: STALE_PERSONAL_KEY, hint: "never read now -- clear it", stale: true});
        }
        return out;
    }, [projects, map, aliases, mySub, myName]);

    const persist = useCallback(async (scope: string, provider: string, enabled: EnabledCollections) => {
        const key = rowKey(scope, provider);
        setBusy(key);
        setError(null);
        // Shown at once, so a tick reads as ticked rather than waiting out the
        // round trip; put back if the write fails.
        let previous: ScopeCollectionsMap | null = null;
        setMap((cur) => {
            previous = cur;
            return withEnabled(cur, scope, provider, enabled);
        });
        try {
            // Applied to the STORED value, not this tab's copy of it: another
            // admin's change since this tab loaded is kept, and only the one
            // (scope, provider) entry being edited is written.
            const latest = parseScopeCollections(
                await viewerApi.getPublicSetting(ASSET_SCOPE_COLLECTIONS_KEY).catch(() => null),
            );
            const next = withEnabled(latest, scope, provider, enabled);
            await viewerApi.adminSetSetting(ASSET_SCOPE_COLLECTIONS_KEY, serialiseScopeCollections(next));
            setMap(next);
        } catch (e) {
            if (previous) setMap(previous);
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(null);
        }
    }, []);

    if (loading) {
        return <div className="px-4 py-8 text-center text-gray-500 text-sm">Loading…</div>;
    }

    return (
        <div className="h-full overflow-auto">
            <div className="px-3 py-3 border-b border-gray-700 space-y-1">
                <div className="text-sm font-medium">Asset providers</div>
                <div className="flex items-start gap-2">
                    <div className="text-xs text-gray-400 flex-1 space-y-1">
                        <p>
                            Which of a provider's collections may be requested in each scope. A scope left on
                            “All collections” behaves as before; “Only ticked” with nothing ticked offers none.
                        </p>
                        <p>
                            A display name renames a provider for one scope's viewers only; empty falls back to
                            the provider's own name, then its id. The id itself never changes.
                        </p>
                        <p>
                            <span className="text-amber-300">This filters a request picker. It is not a permission</span>
                            {" "}— nothing on the server refuses a request for an unticked collection, and it
                            hides nothing already published.
                        </p>
                    </div>
                    <label className="flex items-center gap-1.5 text-xs text-gray-300 shrink-0 py-1">
                        <input
                            type="checkbox"
                            checked={showEnabled}
                            onChange={(e) => toggleShowEnabled(e.target.checked)}
                        />
                        Show enabled projects
                    </label>
                    <button
                        type="button"
                        className="text-xs px-2 py-1 rounded-sm border border-gray-700 hover:bg-gray-800"
                        onClick={() => void refresh()}
                    >
                        Refresh
                    </button>
                </div>
            </div>

            {error && (
                <div className="px-3 py-2 text-red-300 text-xs border-b border-gray-700">{error}</div>
            )}

            {sections.length === 0 && (
                <div className="px-3 py-6 text-sm text-gray-500">
                    No online worker declares an asset provider with collections, and nothing is stored. A
                    backend plugin declares one with <code>asset_provider_id</code> and{" "}
                    <code>asset_collections_field</code> on its spec.
                </div>
            )}

            {sections.map(({providerId, live: declared}) => {
                const advertised = declared?.collections ?? [];
                const refresh = declared?.refresh ?? null;
                const rs = rescanOf(providerId);
                return (
                    <AdminSection
                        key={providerId}
                        kind="Asset provider"
                        storageKey={`ada.admin.providers.open.${providerId}`}
                        title={
                            <>
                                <code>{providerId}</code>
                                {declared?.label && (
                                    <span className="ml-2 text-xs font-normal text-gray-300" title="The provider's own display name">
                                        “{declared.label}”
                                    </span>
                                )}
                                {declared && declared.titles.length > 0 && (
                                    <span className="ml-2 text-xs font-normal text-gray-400">
                                        {declared.titles.join(", ")}
                                    </span>
                                )}
                            </>
                        }
                        subtitle={
                            declared
                                ? `${advertised.length} collection${advertised.length === 1 ? "" : "s"} advertised by ${declared.pluginIds.join(", ") || "an online worker"}`
                                : "No online worker declares this provider right now. Its stored grants are kept, and listed below."
                        }
                        actions={
                            refresh && (
                                // Declared by the provider (`asset_collections_refresh`), so
                                // only a provider that knows how to look again offers it. It
                                // is how a collection the worker missed at boot -- a share that
                                // refused a folder for a moment -- comes back without a restart.
                                <>
                                    {rs?.note && (
                                        <span className={`text-xs ${rs.failed ? "text-red-300" : "text-gray-400"}`}>
                                            {rs.note}
                                        </span>
                                    )}
                                    <button
                                        type="button"
                                        className="text-xs px-2 py-1 rounded-sm border border-gray-700 hover:bg-gray-800 disabled:opacity-50"
                                        disabled={rs?.running}
                                        title={`Ask ${refresh.pluginId} to look for its collections again -- followed in the job toast, so it carries on if you leave this tab`}
                                        onClick={() => void rescan(providerId, refresh, advertised, declared?.label ?? providerId)}
                                    >
                                        {rs?.running ? "Rescanning…" : "Rescan"}
                                    </button>
                                </>
                            )
                        }
                    >
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-left text-xs uppercase text-gray-500">
                                    <th className="px-3 py-2 font-medium">Scope</th>
                                    <th className="px-3 py-2 font-medium">Display name</th>
                                    <th className="px-3 py-2 font-medium">Access</th>
                                    <th className="px-3 py-2 font-medium">Collections</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row) => {
                                    const key = rowKey(row.scope, providerId);
                                    // The stale `user:me` row shows what is STORED (to clear it); every
                                    // other row what it resolves to -- a personal one, nothing by default.
                                    const enabled =
                                        row.scope === STALE_PERSONAL_KEY
                                            ? (map[row.scope]?.[providerId] ?? null)
                                            : enabledFor(map, row.scope, providerId);
                                    const personal = isPersonalKey(row.scope);
                                    const choices = collectionChoices(advertised, enabled);
                                    const unadvertised = choices.filter((c) => !c.advertised).length;
                                    const isOpen = open === key;
                                    return (
                                        <tr key={row.scope} className="border-t border-gray-800">
                                            <td className="px-3 py-2 align-top">
                                                <div className={`font-medium ${row.stale ? "text-gray-400" : ""}`}>
                                                    {row.label}
                                                </div>
                                                <div className="text-xs text-gray-500">{row.hint}</div>
                                            </td>
                                            <td className="px-3 py-2 align-top">
                                                <AliasField
                                                    value={aliases[row.scope]?.[providerId] ?? ""}
                                                    fallback={declared?.label ?? providerId}
                                                    disabled={busy === key}
                                                    label={`Display name of ${providerId} in ${row.label}`}
                                                    onSave={(next) => persistAlias(row.scope, providerId, next)}
                                                />
                                            </td>
                                            <td className="px-3 py-2 align-top">
                                                {personal ? (
                                                    // No "All collections" for a personal scope: missing is
                                                    // nothing there, so only ticks can give it anything.
                                                    <span className="text-xs text-gray-400" title="A personal scope offers nothing until a collection is ticked for it">
                                                        Only ticked (none by default)
                                                    </span>
                                                ) : (
                                                <select
                                                    className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-sm"
                                                    value={enabled === null ? "all" : "ticked"}
                                                    disabled={busy === key}
                                                    aria-label={`Access to ${providerId} in ${row.label}`}
                                                    onChange={(e) => {
                                                        // Restricting starts from NOTHING ticked: an
                                                        // allow-list, read literally. Back to "all"
                                                        // removes the entry, not the ticks' meaning.
                                                        const next = e.target.value === "all" ? null : [];
                                                        void persist(row.scope, providerId, next);
                                                        if (next !== null) setOpen(key);
                                                    }}
                                                >
                                                    <option value="all">All collections</option>
                                                    <option value="ticked">Only ticked</option>
                                                </select>
                                                )}
                                            </td>
                                            <td className="px-3 py-2 align-top">
                                                {enabled === null ? (
                                                    <span className="text-xs text-gray-500">
                                                        Unrestricted — every advertised collection is offered.
                                                    </span>
                                                ) : (
                                                    <div className="space-y-1">
                                                        <button
                                                            type="button"
                                                            className="text-xs px-2 py-0.5 rounded-sm border border-gray-700 hover:bg-gray-800"
                                                            onClick={() => {
                                                                setFilter("");
                                                                setOpen(isOpen ? null : key);
                                                            }}
                                                        >
                                                            {enabled.length === 0
                                                                ? "None enabled"
                                                                : `${enabled.length} enabled`}
                                                            {advertised.length > 0 && ` of ${advertised.length} advertised`}
                                                            {unadvertised > 0 && ` · ${unadvertised} not advertised`}
                                                        </button>
                                                        {showEnabled && enabled.length > 0 && (
                                                            <ul className="inline-flex flex-wrap gap-1 align-middle ml-2" aria-label="Enabled collections">
                                                                {[...enabled].sort().map((c) => {
                                                                    const live = advertised.includes(c);
                                                                    return (
                                                                        <li
                                                                            key={c}
                                                                            className={`rounded-sm px-1.5 py-0.5 text-[10px] font-mono ${
                                                                                live ? "bg-emerald-900/60 text-emerald-100" : "bg-gray-800 text-amber-300"
                                                                            }`}
                                                                            title={live ? c : `${c} — enabled, but no online worker advertises it right now`}
                                                                        >
                                                                            {c.toUpperCase()}
                                                                        </li>
                                                                    );
                                                                })}
                                                            </ul>
                                                        )}
                                                        {isOpen && (
                                                            <div className="rounded-sm border border-gray-700 bg-gray-900/60 p-2 space-y-2 max-w-xl">
                                                                <input
                                                                    type="search"
                                                                    value={filter}
                                                                    onChange={(e) => setFilter(e.target.value)}
                                                                    placeholder={`Filter ${choices.length} collections…`}
                                                                    aria-label="Filter collections"
                                                                    className="w-full rounded-sm border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-100 placeholder:text-gray-500"
                                                                />
                                                                {choices.length === 0 && (
                                                                    <div className="text-[11px] text-gray-500">
                                                                        Nothing is advertised and nothing is enabled.
                                                                    </div>
                                                                )}
                                                                <ul className="max-h-64 overflow-auto grid grid-cols-2 sm:grid-cols-3 gap-x-2">
                                                                    {fuzzyFilter(choices, filter, (c) => c.collection).map((c) => (
                                                                        <li key={c.collection}>
                                                                            <label
                                                                                className="flex items-center gap-2 py-0.5 px-1 text-xs rounded-sm cursor-pointer hover:bg-gray-800/60"
                                                                                title={
                                                                                    c.advertised
                                                                                        ? c.collection
                                                                                        : "Enabled, but no online worker advertises it right now. Kept until unticked."
                                                                                }
                                                                            >
                                                                                <input
                                                                                    type="checkbox"
                                                                                    checked={c.enabled}
                                                                                    disabled={busy === key}
                                                                                    onChange={(e) =>
                                                                                        void persist(
                                                                                            row.scope,
                                                                                            providerId,
                                                                                            toggleCollection(
                                                                                                enabled,
                                                                                                advertised,
                                                                                                c.collection,
                                                                                                e.target.checked,
                                                                                            ),
                                                                                        )
                                                                                    }
                                                                                />
                                                                                <span className="truncate text-gray-200">
                                                                                    {c.collection}
                                                                                </span>
                                                                                {!c.advertised && (
                                                                                    <span className="text-[10px] text-amber-400">
                                                                                        offline
                                                                                    </span>
                                                                                )}
                                                                            </label>
                                                                        </li>
                                                                    ))}
                                                                </ul>
                                                            </div>
                                                        )}
                                                    </div>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </AdminSection>
                );
            })}

            {/* Not an asset provider, but the same question asked of another registry: which of
                the providers a live pool offers does each scope use. Kept in its own component --
                it shares the scope rows and nothing else. */}
            <ClashSpecProvidersSection rows={rows.filter((r) => !r.stale)} />

            {/* The jobs each provider declares for scheduling (`asset_schedules`), per scope and
                collection -- replaces the generic plugin-job schedules under Audit. */}
            <ProviderSchedulesSection rows={rows.filter((r) => !r.stale)} />
        </div>
    );
};

export default ProvidersTab;
