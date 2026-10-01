import React, {useCallback, useEffect, useMemo, useState} from "react";

import {AdminProject, viewerApi} from "@/services/viewerApi";
import {
    ASSET_SCOPE_COLLECTIONS_KEY,
    AssetProviderCollections,
    EnabledCollections,
    ScopeCollectionsMap,
    assetProviderCollections,
    collectionChoices,
    enabledFor,
    parseScopeCollections,
    serialiseScopeCollections,
    storedProviders,
    toggleCollection,
    withEnabled,
} from "@/services/assetScopeCollections";
import {fuzzyFilter} from "@/services/fuzzy";

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
// WHY NOT A ROW IN "External Models". That tab BINDS a scope to one collection
// to show; this GRANTS a set a scope may ask for. A control meaning both ends up
// meaning neither.
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
// Personal scopes are left out: a grant on one has no second party to protect.

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

const ProvidersTab: React.FC = () => {
    const [live, setLive] = useState<AssetProviderCollections[]>([]);
    const [projects, setProjects] = useState<AdminProject[]>([]);
    const [map, setMap] = useState<ScopeCollectionsMap>({});
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState<string | null>(null);
    const [open, setOpen] = useState<string | null>(null);
    const [filter, setFilter] = useState("");
    const [error, setError] = useState<string | null>(null);

    const refresh = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [plugins, projs, raw] = await Promise.all([
                viewerApi.listBackendPlugins().catch(() => ({plugins: []})),
                viewerApi.adminListProjects().catch(() => [] as AdminProject[]),
                viewerApi.getPublicSetting(ASSET_SCOPE_COLLECTIONS_KEY).catch(() => null),
            ]);
            setLive(assetProviderCollections(plugins.plugins ?? []));
            setProjects(projs.filter((p) => !p.archived_at));
            setMap(parseScopeCollections(raw));
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    // Live providers, plus any the stored map names that no worker declares now.
    const sections: ProviderSection[] = useMemo(() => {
        const byId = new Map(live.map((p) => [p.providerId, p]));
        const ids = new Set([...byId.keys(), ...storedProviders(map)]);
        return [...ids].sort().map((providerId) => ({providerId, live: byId.get(providerId) ?? null}));
    }, [live, map]);

    const rows: ScopeRow[] = useMemo(() => {
        const out: ScopeRow[] = [{scope: "shared", label: "Shared", hint: "everyone with access to this viewer"}];
        for (const p of projects) out.push({scope: `project:${p.id}`, label: p.name, hint: p.slug});
        const known = new Set(out.map((r) => r.scope));
        for (const scope of Object.keys(map).sort()) {
            if (!known.has(scope)) out.push({scope, label: scope, hint: "not a current project", stale: true});
        }
        return out;
    }, [projects, map]);

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
                            <span className="text-amber-300">This filters a request picker. It is not a permission</span>
                            {" "}— nothing on the server refuses a request for an unticked collection, and it
                            hides nothing already published.
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
                return (
                    <section key={providerId} className="border-b border-gray-800">
                        <div className="px-3 pt-3 pb-1">
                            <div className="text-sm font-medium">
                                <code>{providerId}</code>
                                {declared && declared.titles.length > 0 && (
                                    <span className="ml-2 text-xs font-normal text-gray-400">
                                        {declared.titles.join(", ")}
                                    </span>
                                )}
                            </div>
                            <div className="text-xs text-gray-500">
                                {declared
                                    ? `${advertised.length} collection${advertised.length === 1 ? "" : "s"} advertised by ${declared.pluginIds.join(", ") || "an online worker"}`
                                    : "No online worker declares this provider right now. Its stored grants are kept, and listed below."}
                            </div>
                        </div>
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-left text-xs uppercase text-gray-500">
                                    <th className="px-3 py-2 font-medium">Scope</th>
                                    <th className="px-3 py-2 font-medium">Access</th>
                                    <th className="px-3 py-2 font-medium">Collections</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row) => {
                                    const key = rowKey(row.scope, providerId);
                                    const enabled = enabledFor(map, row.scope, providerId);
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
                    </section>
                );
            })}
        </div>
    );
};

export default ProvidersTab;
