import React, {useCallback, useEffect, useState} from "react";

import {clashCheckApi} from "@/services/api/clashCheck";
import {
    BUILTIN_SPEC_PROVIDER,
    CLASH_SPEC_PROVIDERS_KEY,
    SpecProviderPreference,
    SpecProvidersMap,
    moveSpecProvider,
    parseSpecProviders,
    preferenceFor,
    serialiseSpecProviders,
    specProviderChoices,
    specProviderLabel,
    toggleSpecProvider,
    withPreference,
} from "@/services/clashSpecProviders";
import {viewerApi} from "@/services/viewerApi";

// Admin → Providers: which joint-detailing spec PROVIDERS each scope uses by default, and in
// which order of preference. The Clashes panel starts from this and may override it for a session.
//
// A provider is the POOL a connection spec is served by -- core never learns the package -- plus
// `builtin` for core's own. The list of pools comes from the live `connection_specs` union, and a
// stored provider no pool advertises right now stays listed and marked: an offline worker is not
// a revocation, the same rule the asset-collection grants above follow.

export interface SpecProviderScopeRow {
    scope: string;
    label: string;
    hint: string;
    stale?: boolean;
}

const ClashSpecProvidersSection: React.FC<{rows: readonly SpecProviderScopeRow[]}> = ({rows}) => {
    const [map, setMap] = useState<SpecProvidersMap>({});
    const [advertised, setAdvertised] = useState<string[]>([]);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        void (async () => {
            const [raw, caps] = await Promise.all([
                viewerApi.getPublicSetting(CLASH_SPEC_PROVIDERS_KEY).catch(() => null),
                clashCheckApi.listLiveConnectionCapabilities("shared" as never).catch(() => new Set<string>()),
            ]);
            setMap(parseSpecProviders(raw));
            setAdvertised([...caps].sort());
        })();
    }, []);

    const persist = useCallback(async (scope: string, pref: SpecProviderPreference) => {
        setBusy(scope);
        setError(null);
        let previous: SpecProvidersMap | null = null;
        setMap((cur) => {
            previous = cur;
            return withPreference(cur, scope, pref);
        });
        try {
            // Applied to the STORED value, so another admin's edit to another scope is kept.
            const latest = parseSpecProviders(
                await viewerApi.getPublicSetting(CLASH_SPEC_PROVIDERS_KEY).catch(() => null),
            );
            const next = withPreference(latest, scope, pref);
            await viewerApi.adminSetSetting(CLASH_SPEC_PROVIDERS_KEY, serialiseSpecProviders(next));
            setMap(next);
        } catch (e) {
            if (previous) setMap(previous);
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(null);
        }
    }, []);

    const allRows = [...rows];
    const known = new Set(rows.map((r) => r.scope));
    for (const scope of Object.keys(map).sort()) {
        if (!known.has(scope)) allRows.push({scope, label: scope, hint: "not a current project", stale: true});
    }

    return (
        <section className="border-b border-gray-800" data-testid="clash-spec-providers-admin">
            <div className="px-3 pt-3 pb-1 space-y-1">
                <div className="text-sm font-medium">Joint detailing spec providers</div>
                <div className="text-xs text-gray-400">
                    Which providers' connection specs detail the joints a clash check finds, first preferred. A
                    scope left on “All providers” offers every spec, ranked by its own priority. The Clashes panel
                    starts from this and can override it for a session.
                </div>
                {advertised.length === 0 && (
                    <div className="text-xs text-gray-500">
                        No live pool advertises a contributed connection spec right now; only{" "}
                        {specProviderLabel(BUILTIN_SPEC_PROVIDER)} specs are available.
                    </div>
                )}
            </div>
            {error && <div className="px-3 py-1 text-red-300 text-xs">{error}</div>}
            <table className="w-full text-sm">
                <thead>
                    <tr className="text-left text-xs uppercase text-gray-500">
                        <th className="px-3 py-2 font-medium">Scope</th>
                        <th className="px-3 py-2 font-medium">Providers</th>
                    </tr>
                </thead>
                <tbody>
                    {allRows.map((row) => {
                        const pref = preferenceFor(map, row.scope);
                        const choices = specProviderChoices(advertised, pref);
                        const enabled = choices.filter((c) => c.enabled).map((c) => c.provider);
                        return (
                            <tr key={row.scope} className="border-t border-gray-800">
                                <td className="px-3 py-2 align-top">
                                    <div className={`font-medium ${row.stale ? "text-gray-400" : ""}`}>{row.label}</div>
                                    <div className="text-xs text-gray-500">{row.hint}</div>
                                </td>
                                <td className="px-3 py-2 align-top space-y-1">
                                    <select
                                        className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-sm"
                                        value={pref === null ? "all" : "ordered"}
                                        disabled={busy === row.scope}
                                        aria-label={`Spec providers in ${row.label}`}
                                        onChange={(e) =>
                                            void persist(
                                                row.scope,
                                                e.target.value === "all"
                                                    ? null
                                                    : // Starting from today's behaviour, made explicit:
                                                      // every provider, contributed ones first.
                                                      [...advertised, BUILTIN_SPEC_PROVIDER],
                                            )
                                        }
                                    >
                                        <option value="all">All providers</option>
                                        <option value="ordered">Chosen, in order</option>
                                    </select>
                                    {pref !== null && (
                                        <ol className="flex flex-wrap gap-1">
                                            {choices.map((c) => {
                                                const rank = enabled.indexOf(c.provider);
                                                return (
                                                    <li
                                                        key={c.provider}
                                                        className={`flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs ${
                                                            c.enabled ? "bg-emerald-900/60 text-emerald-100" : "bg-gray-800 text-gray-400"
                                                        }`}
                                                    >
                                                        <input
                                                            type="checkbox"
                                                            checked={c.enabled}
                                                            disabled={busy === row.scope}
                                                            aria-label={`Use ${specProviderLabel(c.provider)} in ${row.label}`}
                                                            onChange={(e) =>
                                                                void persist(
                                                                    row.scope,
                                                                    toggleSpecProvider(pref, advertised, c.provider, e.target.checked),
                                                                )
                                                            }
                                                        />
                                                        {c.enabled && <span className="text-gray-400">{rank + 1}.</span>}
                                                        {specProviderLabel(c.provider)}
                                                        {!c.advertised && (
                                                            <span className="text-[10px] text-amber-400">offline</span>
                                                        )}
                                                        {c.enabled && rank > 0 && (
                                                            <button
                                                                type="button"
                                                                className="text-gray-400 hover:text-white"
                                                                title="Prefer over the one before it"
                                                                disabled={busy === row.scope}
                                                                onClick={() =>
                                                                    void persist(row.scope, moveSpecProvider(enabled, c.provider, -1))
                                                                }
                                                            >
                                                                ↑
                                                            </button>
                                                        )}
                                                    </li>
                                                );
                                            })}
                                        </ol>
                                    )}
                                    {pref !== null && enabled.length === 0 && (
                                        <div className="text-xs text-amber-300">
                                            No provider enabled — nothing will be offered for detailing in this scope.
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
};

export default ClashSpecProvidersSection;
