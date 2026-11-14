// Admin → Providers: which scopes offer each worker pool's connection specs in the connection
// builder (`public.connection_scopes`, see services/connectionScopes). A pool is offered in every
// scope until it is restricted here; core's built-in specs are always offered. The pools are the
// ones the live workers advertise, plus any stored one no worker advertises right now (an offline
// worker is not a revocation).

import React, {useCallback, useEffect, useState} from "react";

import {clashCheckApi} from "@/services/api/clashCheck";
import {
    CONNECTION_SCOPES_KEY,
    parseConnectionScopes,
    scopesFor,
    serialiseConnectionScopes,
    settingScopeFor,
    toggleScope,
    withScopes,
    type ConnectionScopesMap,
} from "@/services/connectionScopes";
import {viewerApi} from "@/services/viewerApi";

import AdminSection from "./AdminSection";
import type {SpecProviderScopeRow} from "./ClashSpecProvidersSection";

const ConnectionScopesSection: React.FC<{rows: readonly SpecProviderScopeRow[]}> = ({rows}) => {
    const [map, setMap] = useState<ConnectionScopesMap>({});
    const [advertised, setAdvertised] = useState<string[]>([]);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        void (async () => {
            const [raw, caps] = await Promise.all([
                viewerApi.getPublicSetting(CONNECTION_SCOPES_KEY).catch(() => null),
                clashCheckApi.listLiveConnectionCapabilities("shared" as never).catch(() => new Set<string>()),
            ]);
            setMap(parseConnectionScopes(raw));
            setAdvertised([...caps].sort());
        })();
    }, []);

    const persist = useCallback(async (capability: string, change: (m: ConnectionScopesMap) => ConnectionScopesMap) => {
        setBusy(capability);
        setError(null);
        try {
            // Applied to the STORED value, so another admin's edit to another pool is kept.
            const latest = parseConnectionScopes(await viewerApi.getPublicSetting(CONNECTION_SCOPES_KEY).catch(() => null));
            const next = change(latest);
            await viewerApi.adminSetSetting(CONNECTION_SCOPES_KEY, serialiseConnectionScopes(next));
            setMap(next);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(null);
        }
    }, []);

    const capabilities = [...new Set([...advertised, ...Object.keys(map)])].sort();
    // One row per scope the setting can name: a personal scope stands for all of them.
    const scopeRows = rows.map((r) => ({...r, key: settingScopeFor(r.scope)}));
    const personal = scopeRows.find((r) => r.key === "user");
    if (personal) {
        personal.label = "Personal scopes";
        personal.hint = "every user's own scope";
    }

    return (
        <AdminSection
            title="Connection builder: specs per scope"
            kind="Connection specs"
            storageKey="ada.admin.providers.open.connection-scopes"
            testId="connection-scopes-admin"
        >
            <div className="px-3 pt-2 pb-1 text-xs text-gray-400">
                Which scopes the connection builder offers each pool's specs in. The specs are the ones the
                pool's workers have registered in code. A pool stays offered in every scope until you limit it here.
                Core's built-in specs are always offered.
            </div>
            {capabilities.length === 0 && (
                <div className="px-3 pb-2 text-xs text-gray-500">No live pool advertises a contributed connection spec right now.</div>
            )}
            {error && <div className="px-3 py-1 text-red-300 text-xs">{error}</div>}
            <div className="divide-y divide-gray-800">
                {capabilities.map((cap) => {
                    const scopes = scopesFor(map, cap);
                    const online = advertised.includes(cap);
                    return (
                        <div key={cap} className="px-3 py-2 space-y-1" data-connection-scopes={cap}>
                            <div className="flex items-center gap-2 text-sm">
                                <span className="font-medium">{cap}</span>
                                {!online && <span className="text-xs text-yellow-300">no live worker</span>}
                                <span className="flex-1" />
                                <select
                                    className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-sm"
                                    value={scopes === null ? "all" : "some"}
                                    disabled={busy === cap}
                                    aria-label={`Scopes offering ${cap}'s connection specs`}
                                    onChange={(e) =>
                                        void persist(cap, (m) => withScopes(m, cap, e.target.value === "all" ? null : []))
                                    }
                                >
                                    <option value="all">Every scope</option>
                                    <option value="some">Only the scopes ticked</option>
                                </select>
                            </div>
                            {scopes !== null && (
                                <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                                    {scopeRows.map((row) => (
                                        <label key={row.key} className="flex items-center gap-1.5" title={row.hint}>
                                            <input
                                                type="checkbox"
                                                checked={scopes.includes(row.key)}
                                                disabled={busy === cap}
                                                onChange={(e) => void persist(cap, (m) => toggleScope(m, cap, row.key, e.target.checked))}
                                            />
                                            <span className={row.stale ? "text-gray-400" : ""}>{row.label}</span>
                                        </label>
                                    ))}
                                    {scopes.length === 0 && <span className="text-xs text-yellow-300">offered nowhere</span>}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </AdminSection>
    );
};

export default ConnectionScopesSection;
