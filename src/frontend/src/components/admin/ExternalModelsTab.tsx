import React, {useCallback, useEffect, useMemo, useState} from "react";

import {AdminProject, viewerApi} from "@/services/viewerApi";
import {
    ExternalCollection,
    ExternalModelProvider,
    WEB3D_PROVIDER_ID,
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
    isHidden,
    serialiseBinding,
} from "@/services/externalModelsBinding";
import type {ExternalModel} from "@/services/externalModels";
import {listModels} from "@/services/externalModels";
import {DataTable, DataTableColumn} from "@/components/common/DataTable";
import Web3dMirrorPanel from "@/components/admin/Web3dMirrorPanel";

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
    // The scope whose model filter is being edited, and the draft text. One at a
    // time: the editor carries a live preview, and previewing several at once
    // would mean holding several collections' model lists.
    const [hideScope, setHideScope] = useState<string | null>(null);
    const [hideDraft, setHideDraft] = useState("");
    // The bound collection's models, for that preview. Fetched when the editor
    // opens, because a filter whose effect nobody can see is exactly the
    // fragile thing it is meant to replace.
    const [hidePreview, setHidePreview] = useState<ExternalModel[] | null>(null);
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

    const openHideEditor = useCallback(
        async (scope: string) => {
            const bound = bindingFor(map, scope);
            if (!bound) return;
            setHideScope(scope);
            setHideDraft(bound.hide.join(", "));
            setHidePreview(null);
            try {
                setHidePreview(
                    await listModels(bound.provider, bound.collection, CATALOGUE_SCOPE, {
                        refresh: catalogueNonce(),
                    }),
                );
            } catch {
                // A preview that cannot be fetched is no reason to refuse the
                // edit: the patterns are still valid, they just cannot be
                // counted here. [] renders as "cannot preview".
                setHidePreview([]);
            }
        },
        [map],
    );

    const saveHide = useCallback(
        async (scope: string, patterns: string[]) => {
            const bound = bindingFor(map, scope);
            if (!bound) return;
            setBusy(scope);
            setError(null);
            try {
                const next = {...map};
                next[scope] = serialiseBinding({
                    provider: bound.provider,
                    collection: bound.collection,
                    hide: patterns,
                });
                await persist(next);
            } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
            } finally {
                setBusy(null);
            }
        },
        [map, persist],
    );

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

    // Per-row derived state, computed once per cell render. A persisted
    // binding wins; otherwise show what the operator just picked and has not
    // finished. ``known`` is undefined until fetched; [] once fetched and
    // empty — passing the raw value through matters, see boundCollectionOption.
    // A binding whose collection the list does not carry still renders:
    // dropping it from the <select> would silently unbind the scope the
    // moment an admin opened this tab before the provider had been read.
    const rowState = (row: ScopeRow) => {
        const bound = bindingFor(map, row.scope);
        const provider = bound?.provider ?? pendingProvider[row.scope] ?? "";
        const collection = bound?.collection ?? "";
        const known = collections[provider];
        const orphan = boundCollectionOption(collection, known);
        return {bound, provider, collection, known, orphan};
    };
    const bindingColumns: DataTableColumn<ScopeRow>[] = [
        {
            key: "scope",
            header: "Scope",
            cell: (row) => (
                <>
                    <div className="font-medium">{row.label}</div>
                    <div className="text-xs text-gray-500">{row.hint}</div>
                </>
            ),
        },
        {
            key: "provider",
            header: "Provider",
            cell: (row) => {
                const {bound, provider} = rowState(row);
                return (
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
                );
            },
        },
        {
            key: "collection",
            header: "Collection",
            cell: (row) => {
                const {provider, collection, known, orphan} = rowState(row);
                return (
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
                );
            },
        },
        {
            key: "hide",
            header: "Hidden models",
            cellClassName: "px-3 py-2 align-top",
            cell: (row) => {
                const bound = bindingFor(map, row.scope);
                if (!bound) return <span className="text-xs text-gray-600">—</span>;
                return (
                    <div className="space-y-1">
                        <button
                            type="button"
                            className="text-xs px-2 py-0.5 rounded-sm border border-gray-700 hover:bg-gray-800"
                            disabled={busy === row.scope}
                            onClick={() => {
                                if (hideScope === row.scope) {
                                    setHideScope(null);
                                } else {
                                    void openHideEditor(row.scope);
                                }
                            }}
                        >
                            {bound.hide.length === 0
                                ? "None hidden"
                                : `${bound.hide.length} pattern${bound.hide.length === 1 ? "" : "s"}`}
                        </button>
                        {hideScope === row.scope && (
                            <div className="space-y-1">
                                {/* FREE TEXT, deliberately, and previewed for the
                                    same reason. A pattern is not something that
                                    can be ticked from a list -- the whole value
                                    is matching a family of names at once -- but
                                    an unchecked pattern is a guess. The count
                                    below turns a typo into something visible
                                    instead of a scope that silently shows
                                    everything. */}
                                <input
                                    className="w-full text-xs bg-gray-900 border border-gray-700 rounded-sm px-2 py-1"
                                    placeholder="TempSteel, *_VAT, *Volumes.rvm*"
                                    value={hideDraft}
                                    onChange={(e) => setHideDraft(e.target.value)}
                                />
                                {(() => {
                                    const patterns = hideDraft
                                        .split(",")
                                        .map((p: string) => p.trim())
                                        .filter(Boolean);
                                    if (hidePreview === null) {
                                        return (
                                            <div className="text-[11px] text-gray-500">
                                                Reading the collection…
                                            </div>
                                        );
                                    }
                                    if (hidePreview.length === 0) {
                                        return (
                                            <div className="text-[11px] text-gray-500">
                                                Cannot preview this collection; the patterns still apply.
                                            </div>
                                        );
                                    }
                                    const hit = hidePreview.filter((m) => isHidden(m, patterns));
                                    return (
                                        <div className="text-[11px] text-gray-500">
                                            hides {hit.length} of {hidePreview.length}
                                            {hit.length > 0 && (
                                                <span className="text-gray-600">
                                                    {" — "}
                                                    {hit.slice(0, 3).map((m) => m.name).join(", ")}
                                                    {hit.length > 3 ? `, +${hit.length - 3}` : ""}
                                                </span>
                                            )}
                                            {patterns.length > 0 && hit.length === 0 && (
                                                <span className="text-amber-300"> — matches nothing</span>
                                            )}
                                        </div>
                                    );
                                })()}
                                <div className="flex gap-1">
                                    <button
                                        type="button"
                                        className="text-xs px-2 py-0.5 rounded-sm border border-gray-700 hover:bg-gray-800"
                                        disabled={busy === row.scope}
                                        onClick={() => {
                                            void saveHide(
                                                row.scope,
                                                hideDraft.split(",").map((p: string) => p.trim()).filter(Boolean),
                                            );
                                            setHideScope(null);
                                        }}
                                    >
                                        Save
                                    </button>
                                    <button
                                        type="button"
                                        className="text-xs px-2 py-0.5 rounded-sm border border-gray-700 hover:bg-gray-800"
                                        onClick={() => setHideScope(null)}
                                    >
                                        Cancel
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>
                );
            },
        },
    ];

    // WHICH PROVIDER HOLDS THE CACHE: the web3d one, by name.
    //
    // It used to be "whatever `shared` is bound to, else the first registered",
    // which is wrong in the ordinary case. A deployment binds `shared` to its
    // OBJECT STORE -- that is the point of the binding -- and the panel then
    // asked the object store which web3d projects it could mirror, getting
    //
    //     provider 'object-store' does not know what it could mirror; only a
    //     provider backed by an upstream catalogue can answer that
    //
    // which is the backend correctly refusing a question meant for someone
    // else. Being bound is not the same property as owning an upstream mirror,
    // and only the second one matters here.
    //
    // Matched against the registered ids, so a deployment without the provider
    // gets no panel rather than a panel full of refusals.
    const mirrorProvider = providers.find((p) => p.id === WEB3D_PROVIDER_ID)?.id ?? "";

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

            {mirrorProvider && <Web3dMirrorPanel provider={mirrorProvider} />}

            <DataTable
                wrap={false}
                columns={bindingColumns}
                rows={rows}
                rowKey={(row) => row.scope}
                className="w-full text-sm"
                headerRowClassName="text-left text-xs uppercase text-gray-500"
                headerCellClassName="px-3 py-2 font-medium"
                cellClassName="px-3 py-2 align-top"
                rowClassName="border-t border-gray-800"
            />
        </div>
    );
};

export default ExternalModelsTab;
