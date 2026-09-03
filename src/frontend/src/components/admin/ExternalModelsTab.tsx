import React, {useCallback, useEffect, useMemo, useState} from "react";

import {AdminProject, viewerApi} from "@/services/viewerApi";
import {
    ExternalCollection,
    ExternalModelProvider,
    catalogueNonce,
    listCollections,
    listProviders,
} from "@/services/externalModels";
import {
    ExternalModelBindingMap,
    bindingFor,
    boundCollectionOption,
    parseBindingMap,
    EXTERNAL_MODELS_BINDING_KEY,
} from "@/services/externalModelsBinding";

// Admin tab — bind a viewer scope to an external model collection.
//
// WHY ITS OWN TAB RATHER THAN A ROW IN "Projects". The binding is per SCOPE,
// and a scope is not always a project: `shared` and a user's personal scope are
// bindable too, and neither has a row in the projects list. Hanging this off
// the project rows would have made the two most useful bindings unreachable.
//
// WHAT IT UNLOCKS. The viewer only offers the external-model list for a scope
// that is bound — an unbound deployment sees no change at all. This tab is the
// only way to create that binding.
//
// The map lives at a `public.`-prefixed setting so any authenticated user's UI
// can READ it (they need it to know whether to show the menu entry), while
// writes stay admin-only. It is not sensitive: it says which scope points at
// which collection, and anyone who can see the list already sees those names.

const CATALOGUE_SCOPE = "shared";

interface ScopeRow {
    scope: string;
    label: string;
    hint: string;
}

const ExternalModelsTab: React.FC = () => {
    const [providers, setProviders] = useState<ExternalModelProvider[]>([]);
    const [projects, setProjects] = useState<AdminProject[]>([]);
    const [map, setMap] = useState<ExternalModelBindingMap>({});
    // provider id -> its collections, fetched lazily and cached: each call is an
    // enqueue/poll round-trip, so re-fetching per row would be visibly slow.
    const [collections, setCollections] = useState<Record<string, ExternalCollection[]>>({});
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState<string | null>(null);
    // Provider chosen for a scope but not yet persisted, because a binding needs
    // BOTH halves. Without this the provider <select> appears dead: choosing one
    // would write an incomplete binding, which `setBinding` correctly treats as
    // "unbind", so the control snapped straight back to none.
    const [pendingProvider, setPendingProvider] = useState<Record<string, string>>({});
    // Cache-busting token, refreshed on mount and on demand. Without it the
    // catalogue reads cache-hit forever and this tab cannot show a deployment
    // whose provider configuration changed after the first ever read.
    const [nonce, setNonce] = useState(catalogueNonce);
    const [error, setError] = useState<string | null>(null);

    const refresh = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [provs, projs, raw] = await Promise.all([
                listProviders(CATALOGUE_SCOPE, {refresh: nonce}).catch(
                    () => [] as ExternalModelProvider[],
                ),
                viewerApi.adminListProjects().catch(() => [] as AdminProject[]),
                viewerApi.getPublicSetting(EXTERNAL_MODELS_BINDING_KEY).catch(() => null),
            ]);
            setProviders(provs);
            setProjects(projs.filter((p) => !p.archived_at));
            setMap(parseBindingMap(raw));
            if (provs.length === 0) {
                // Distinguish "no provider registered" from "catalogue empty":
                // the usual cause is a worker that never preloaded a provider
                // module, and that is not visible from anywhere else in the UI.
                setError(
                    "No external-model providers are registered. A worker must preload a " +
                    "provider module before anything can be bound.",
                );
            }
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, [nonce]);

    useEffect(() => {
        void refresh();
    }, [refresh]);


    const loadCollections = useCallback(
        async (provider: string) => {
            if (!provider || collections[provider]) return;
            try {
                const cols = await listCollections(provider, CATALOGUE_SCOPE, {refresh: nonce});
                setCollections((prev) => ({...prev, [provider]: cols}));
            } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
                setCollections((prev) => ({...prev, [provider]: []}));
            }
        },
        [collections, nonce],
    );

    // Collections are otherwise fetched on focus, which is too late for a row
    // that is ALREADY bound: it has to render its collection's name before
    // anyone touches it. Only providers that appear in a binding are fetched,
    // so this stays the same handful of round-trips the lazy path would make,
    // just earlier. loadCollections is a no-op once a provider is cached.
    const boundProviders = useMemo(
        () => JSON.stringify(Array.from(new Set(
            Object.keys(map)
                .map((scope) => bindingFor(map, scope)?.provider)
                .filter((prov): prov is string => Boolean(prov)),
        )).sort()),
        [map],
    );
    useEffect(() => {
        for (const prov of JSON.parse(boundProviders) as string[]) {
            void loadCollections(prov);
        }
    }, [boundProviders, loadCollections]);

    const rows: ScopeRow[] = useMemo(() => {
        const out: ScopeRow[] = [
            {scope: "shared", label: "Shared", hint: "everyone with access to this viewer"},
            {scope: "user:me", label: "My personal scope", hint: "resolved per user, server-side"},
        ];
        for (const p of projects) {
            out.push({scope: `project:${p.id}`, label: p.name, hint: p.slug});
        }
        return out;
    }, [projects]);

    const persist = useCallback(async (next: ExternalModelBindingMap) => {
        // Write through the ADMIN setter: the public prefix governs read access
        // only and has no public setter.
        await viewerApi.adminSetSetting(EXTERNAL_MODELS_BINDING_KEY, JSON.stringify(next));
        setMap(next);
    }, []);

    const setBinding = useCallback(
        async (scope: string, provider: string, collection: string) => {
            setBusy(scope);
            setError(null);
            try {
                const next = {...map};
                if (!provider || !collection) {
                    delete next[scope];
                } else {
                    next[scope] = `${provider}:${collection}`;
                }
                await persist(next);
            } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
            } finally {
                setBusy(null);
            }
        },
        [map, persist],
    );

    if (loading) {
        return <div className="px-4 py-8 text-center text-gray-500 text-sm">Loading…</div>;
    }

    return (
        <div className="h-full overflow-auto">
            <div className="px-3 py-3 border-b border-gray-700 space-y-1">
                <div className="text-sm font-medium">External model bindings</div>
                <div className="flex items-center gap-2">
                    <div className="text-xs text-gray-400 flex-1">
                        A scope with a binding gets an external-model list in the viewer. An unbound
                        scope sees no change.
                    </div>
                    <button
                        type="button"
                        className="text-xs px-2 py-1 rounded-sm border border-gray-700 hover:bg-gray-800"
                        onClick={() => {
                            // New token AND drop the collection cache: both are
                            // keyed on the old one.
                            setCollections({});
                            setNonce(catalogueNonce());
                        }}
                    >
                        Refresh
                    </button>
                </div>
            </div>

            {error && (
                <div className="px-3 py-2 text-red-300 text-xs border-b border-gray-700">{error}</div>
            )}

            <table className="w-full text-sm">
                <thead>
                <tr className="text-left text-xs uppercase text-gray-500">
                    <th className="px-3 py-2 font-medium">Scope</th>
                    <th className="px-3 py-2 font-medium">Provider</th>
                    <th className="px-3 py-2 font-medium">Collection</th>
                </tr>
                </thead>
                <tbody>
                {rows.map((row) => {
                    const bound = bindingFor(map, row.scope);
                    // A persisted binding wins; otherwise show what the operator
                    // just picked and has not finished.
                    const provider = bound?.provider ?? pendingProvider[row.scope] ?? "";
                    const collection = bound?.collection ?? "";
                    // undefined until fetched; [] once fetched and empty. Passing the
                    // raw value through matters — see boundCollectionOption.
                    const known = collections[provider];
                    // A binding whose collection the list does not carry still
                    // renders. Dropping it from the <select> would silently
                    // unbind the scope the moment an admin opened this tab
                    // before the provider had been read.
                    const orphan = boundCollectionOption(collection, known);
                    return (
                        <tr key={row.scope} className="border-t border-gray-800">
                            <td className="px-3 py-2 align-top">
                                <div className="font-medium">{row.label}</div>
                                <div className="text-xs text-gray-500">{row.hint}</div>
                            </td>
                            <td className="px-3 py-2 align-top">
                                <select
                                    className="w-full bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-sm"
                                    value={provider}
                                    disabled={busy === row.scope}
                                    onFocus={() => void loadCollections(provider)}
                                    onChange={(e) => {
                                        const p = e.target.value;
                                        setPendingProvider((prev) => ({...prev, [row.scope]: p}));
                                        void loadCollections(p);
                                        // Only touch storage when the scope was
                                        // already bound: switching provider
                                        // invalidates the old collection (an id is
                                        // only meaningful within its provider), and
                                        // clearing to none means unbind. Choosing a
                                        // provider for an UNBOUND scope writes
                                        // nothing until a collection follows.
                                        if (bound) void setBinding(row.scope, "", "");
                                    }}
                                >
                                    <option value="">— none —</option>
                                    {providers.map((p) => (
                                        <option key={p.id} value={p.id}>{p.label}</option>
                                    ))}
                                </select>
                            </td>
                            <td className="px-3 py-2 align-top">
                                <select
                                    className="w-full bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-sm"
                                    value={collection}
                                    disabled={!provider || busy === row.scope}
                                    onFocus={() => void loadCollections(provider)}
                                    onChange={(e) => void setBinding(row.scope, provider, e.target.value)}
                                    title={provider ? undefined : "Choose a provider first"}
                                >
                                    <option value="">— none —</option>
                                    {orphan && (
                                        <option value={orphan.value}>{orphan.label}</option>
                                    )}
                                    {(known ?? []).map((c) => (
                                        <option key={c.id} value={c.id}>{c.name}</option>
                                    ))}
                                </select>
                            </td>
                        </tr>
                    );
                })}
                </tbody>
            </table>
        </div>
    );
};

export default ExternalModelsTab;
