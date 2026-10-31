import React, {useCallback, useEffect, useMemo, useState} from "react";

import {AdminProject, viewerApi} from "@/services/viewerApi";
import {
    AssetCollectionsRefresh,
    AssetProviderCollections,
    assetProviderCollections,
    STALE_PERSONAL_KEY,
    isPersonalKey,
    personalKey,
} from "@/services/assetScopeCollections";
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
import {sortProjectsBy} from "@/utils/sortProjects";

import AdminSection from "./AdminSection";
import ClashSpecProvidersSection from "./ClashSpecProvidersSection";
import ProviderAccessSection from "./ProviderAccessSection";
import ProviderSchedulesSection from "./ProviderSchedulesSection";
import ReferenceDataSection from "./ReferenceDataSection";

// Admin tab — the asset providers: what each advertises (and a rescan of it), the display name it
// has in each scope, its scheduled jobs, and -- in Access -- which projects each scope may read.
//
// WHERE THE LIST COMES FROM. Not from core's asset-provider registry: a
// provider's collections exist only on the machine that can reach them, so a
// registry in the API process cannot answer. A backend plugin spec instead
// DECLARES the link -- `asset_provider_id` plus `asset_collections_field`, the
// name of the spec field listing the collections -- and this tab reads that off
// `GET /plugins`. Core names no provider here and needs no protocol for it.
//
// WHICH SCOPE READS WHICH COLLECTION is the provider grants, one place: the Access section. The
// per-scope collection matrix this tab used to hold (`public.assets.scope_collections`) was only a
// request-picker filter beside them; it is migrated into grants at startup and no longer read.
//
// YOUR PERSONAL SCOPE IS ONE ROW, keyed `user:<your sub>`: an admin can name providers for their OWN
// personal scope, and nobody else's is listed or touched by it. A `user:me` key (an earlier version
// keyed every user's personal scope at once by it) is never read; it is listed as stale so it can
// be cleared.

interface ScopeRow {
    scope: string;
    label: string;
    hint: string;
    /** Named in the stored display names but not a current scope: an archived or deleted
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
    const [aliases, setAliases] = useState<ProviderAliasesMap>({});
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [rescans, setRescans] = useState<Record<string, RescanState>>({});
    // The signed-in admin: their own personal scope is the one personal row.
    const mySub = useMeStore((s) => s.sub);
    const myName = useMeStore((s) => s.displayName || s.email);

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
            const [plugins, projs, rawAliases] = await Promise.all([
                viewerApi.listBackendPlugins().catch(() => ({plugins: []})),
                viewerApi.adminListProjects().catch(() => [] as AdminProject[]),
                viewerApi.getPublicSetting(PROVIDER_LABELS_KEY).catch(() => null),
            ]);
            setLive(assetProviderCollections(plugins.plugins ?? []));
            setProjects(projs.filter((p) => !p.archived_at));
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

    // Live providers, plus any a stored display name names that no worker declares now.
    const sections: ProviderSection[] = useMemo(() => {
        const byId = new Map(live.map((p) => [p.providerId, p]));
        const aliased = Object.values(aliases).flatMap((row) => Object.keys(row));
        const ids = new Set([...byId.keys(), ...aliased]);
        return [...ids].sort().map((providerId) => ({providerId, live: byId.get(providerId) ?? null}));
    }, [live, aliases]);

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
        if (mySub) out.push({scope: personalKey(mySub), label: "Your personal scope", hint: `${myName || mySub} only`});
        // "Shared" (and the personal scope) stay first by design; the projects, then any stale
        // stored scopes, alphabetically.
        for (const p of sortProjectsBy(projects, (x) => x.name)) out.push({scope: `project:${p.id}`, label: p.name, hint: p.slug});
        const known = new Set(out.map((r) => r.scope));
        for (const scope of Object.keys(aliases).sort()) {
            // Another user's personal scope is theirs (another admin naming their own): not listed here.
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
        return out;
    }, [projects, aliases, mySub, myName]);

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
                            What each provider advertises, and the name it has in each scope. Which projects a scope
                            may read is set under <span className="text-gray-200">Access</span>, below.
                        </p>
                        <p>
                            A display name renames a provider for one scope's viewers only; empty falls back to
                            the provider's own name, then its id. The id itself never changes.
                        </p>
                    </div>
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
                                : "No online worker declares this provider right now. Its display names are kept, and its grants under Access."
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
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row) => {
                                    const key = rowKey(row.scope, providerId);
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

            {/* The jobs each provider declares for scheduling (`asset_schedules`), per collection --
                replaces the generic plugin-job schedules under Audit. */}
            <ProviderSchedulesSection />

            {/* Which scopes read the provider store, and what of it (attributes, geometry). */}
            <ProviderAccessSection projects={projects} />

            {/* What each collection offers for a new element to point at (specs, SPRE, MATR). */}
            <ReferenceDataSection />
        </div>
    );
};

export default ProvidersTab;
