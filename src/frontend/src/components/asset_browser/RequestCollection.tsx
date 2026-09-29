// "Request from a provider": ask a provider for one of its collections, and
// publish it into this scope, from the Assets tab.
//
// WHAT IS OFFERED is what the live workers advertise, narrowed by the scope's
// grants (Admin ▸ Providers, `public.assets.scope_collections`): only a provider
// whose spec declares `asset_collection_request` appears, and only the
// collections this scope may ask for. A grant is a picker filter, and this is
// the picker.
//
// THE FLOW is `@/assets/collectionRequest`: the provider's job stages, core
// publishes. Both jobs go to the toast, so a request can outlive this panel;
// one that finishes unwatched is under "Staged, not published" below, from
// `GET /assets/staging`, where it can be published by hand.

import React, { useCallback, useEffect, useMemo, useState } from "react";

import { requestCollection, type CollectionRequestDeps } from "@/assets/collectionRequest";
import { makePluginContextStandalone } from "@/plugins";
import { assetsApi } from "@/services/api/assets";
import type { ScopeUrl } from "@/services/api/client";
import { conversionApi } from "@/services/api/conversion";
import {
    ASSET_SCOPE_COLLECTIONS_KEY,
    assetProviderCollections,
    enabledFor,
    isCollectionEnabled,
    parseScopeCollections,
    type AssetProviderCollections,
    type ScopeCollectionsMap,
} from "@/services/assetScopeCollections";
import { viewerApi } from "@/services/viewerApi";
import { useMeStore } from "@/state/meStore";

const OWNER = "assets";

type Staged = Awaited<ReturnType<typeof assetsApi.listStaging>>["staged"][number];

function deps(onStage: (s: string) => void): CollectionRequestDeps {
    return {
        api: {
            pluginJob: (pluginId, body, opts) => viewerApi.pluginJob(pluginId, body, { scope: opts.scope as ScopeUrl }),
            publish: (scope, body) => assetsApi.publishStaged(scope as ScopeUrl, body),
            async jobStatus(jobId) {
                const st = await conversionApi.convertStatus(jobId);
                return { status: st.status, stage: st.stage, error: st.error };
            },
            readJson: (scope, key) => assetsApi.getBuildSummary(scope as ScopeUrl, key),
        },
        trackJob: (opts) => {
            makePluginContextStandalone(OWNER).trackJob(opts);
        },
        onStage,
    };
}

function formatSize(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} kB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const RequestCollection: React.FC<{
    scope: string;
    /** Called with the collection a request published, so the tab can show it. */
    onPublished: (collection: string) => void;
}> = ({ scope, onPublished }) => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const [providers, setProviders] = useState<AssetProviderCollections[] | null>(null);
    const [grants, setGrants] = useState<ScopeCollectionsMap>({});
    const [staged, setStaged] = useState<Staged[]>([]);
    const [providerId, setProviderId] = useState("");
    const [collection, setCollection] = useState("");
    const [busy, setBusy] = useState(false);
    const [stage, setStage] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState<string | null>(null);

    const load = useCallback(async () => {
        const [plugins, raw, stagedList] = await Promise.all([
            viewerApi.listBackendPlugins().catch(() => ({ plugins: [] })),
            viewerApi.getPublicSetting(ASSET_SCOPE_COLLECTIONS_KEY).catch(() => null),
            assetsApi.listStaging(scope as ScopeUrl).catch(() => ({ staged: [] })),
        ]);
        const requestable = assetProviderCollections(plugins.plugins ?? []).filter((p) => p.request);
        setProviders(requestable);
        setGrants(parseScopeCollections(raw));
        setStaged(stagedList.staged ?? []);
        setProviderId((cur) => (requestable.some((p) => p.providerId === cur) ? cur : requestable[0]?.providerId ?? ""));
    }, [scope]);

    useEffect(() => {
        setProviders(null);
        void load();
    }, [load]);

    const provider = providers?.find((p) => p.providerId === providerId) ?? null;
    const enabled = provider ? enabledFor(grants, scope, provider.providerId) : null;
    const offered = useMemo(
        () => (provider ? provider.collections.filter((c) => isCollectionEnabled(enabled, c)) : []),
        [provider, enabled],
    );
    useEffect(() => {
        if (!offered.includes(collection)) setCollection(offered[0] ?? "");
    }, [offered, collection]);

    const run = useCallback(
        async (what: () => Promise<string>) => {
            setBusy(true);
            setError(null);
            setDone(null);
            try {
                const published = await what();
                setDone(`Published ${published}.`);
                onPublished(published);
            } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
            } finally {
                setBusy(false);
                setStage(null);
                void load();
            }
        },
        [load, onPublished],
    );

    if (providers === null) return <div className="p-2 text-xs text-gray-400">Reading providers…</div>;
    if (providers.length === 0 && staged.length === 0) return null;

    const request = provider?.request ?? null;
    const blockedForUser = !!request?.requiresAdmin && !isAdmin;

    return (
        <div className="px-2 py-2 space-y-1.5 border-b border-gray-700/70 text-xs">
            {provider && request && (
                <>
                    <div className="text-gray-400">Request from a provider</div>
                    <div className="flex flex-wrap items-center gap-1">
                        {providers.length > 1 && (
                            <select
                                aria-label="Provider"
                                className="h-7 rounded-md border border-gray-700 bg-gray-800 text-xs text-gray-100 px-2"
                                value={providerId}
                                disabled={busy}
                                onChange={(e) => setProviderId(e.target.value)}
                            >
                                {providers.map((p) => (
                                    <option key={p.providerId} value={p.providerId}>
                                        {p.providerId}
                                    </option>
                                ))}
                            </select>
                        )}
                        <select
                            aria-label="Collection to request"
                            className="h-7 rounded-md border border-gray-700 bg-gray-800 text-xs text-gray-100 px-2 min-w-0 max-w-[45%]"
                            value={collection}
                            disabled={busy || offered.length === 0}
                            onChange={(e) => setCollection(e.target.value)}
                        >
                            {offered.map((c) => (
                                <option key={c} value={c}>
                                    {c}
                                </option>
                            ))}
                        </select>
                        <button
                            type="button"
                            className="h-7 px-3 rounded-md text-xs font-semibold bg-blue-400 text-gray-950 hover:bg-blue-300 disabled:opacity-50"
                            disabled={busy || !collection || blockedForUser}
                            title={
                                blockedForUser
                                    ? `${request.pluginId} only takes requests from an admin`
                                    : `Ask ${request.pluginId} for ${collection || "a collection"}, then publish it here`
                            }
                            onClick={() =>
                                void run(async () => {
                                    const out = await requestCollection(
                                        deps(setStage),
                                        scope,
                                        provider.providerId,
                                        request,
                                        collection,
                                    );
                                    return out.collection;
                                })
                            }
                        >
                            {busy ? "Working…" : request.label}
                        </button>
                    </div>
                    {enabled !== null && (
                        <div className="text-gray-500">
                            {offered.length === 0
                                ? "Nothing from this provider is enabled for this scope. An admin enables collections under Admin ▸ Providers."
                                : `${offered.length} of ${provider.collections.length} advertised enabled for this scope.`}
                        </div>
                    )}
                    {provider.collections.length === 0 && (
                        <div className="text-gray-500">No online worker advertises this provider's collections right now.</div>
                    )}
                </>
            )}
            {stage && <div className="text-gray-300">{stage}…</div>}
            {error && <div className="text-red-300 break-words">{error}</div>}
            {done && <div className="text-green-300">{done}</div>}

            {staged.length > 0 && (
                <details>
                    <summary className="cursor-pointer text-gray-400">
                        Staged, not published ({staged.length})
                    </summary>
                    <ul className="mt-1 space-y-1">
                        {staged.map((s) => (
                            <li key={s.staging_id} className="flex items-center gap-2">
                                <code className="truncate text-gray-300" title={s.files.map((f) => f.key).join("\n")}>
                                    {s.staging_id}
                                </code>
                                <span className="text-gray-500 truncate">
                                    {s.files.map((f) => f.file).join(", ")} · {formatSize(s.size)}
                                </span>
                                {providers.length > 0 && (
                                    <button
                                        type="button"
                                        className="ml-auto h-6 px-2 rounded-md border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50"
                                        disabled={busy}
                                        title={`Publish as ${providerId}`}
                                        onClick={() =>
                                            void run(async () => {
                                                setStage(`publishing ${s.staging_id}`);
                                                const job = await assetsApi.publishStaged(scope as ScopeUrl, {
                                                    provider: providerId,
                                                    staging_id: s.staging_id,
                                                });
                                                makePluginContextStandalone(OWNER).trackJob({
                                                    jobId: job.job_id,
                                                    label: `Publish ${s.staging_id}`,
                                                    derivedKey: job.derived_key,
                                                });
                                                for (;;) {
                                                    const st = await conversionApi.convertStatus(job.job_id);
                                                    if (st.status === "done") break;
                                                    if (st.status === "error" || st.status === "cancelled") {
                                                        throw new Error(`the publish ${st.status}: ${st.error ?? ""}`);
                                                    }
                                                    await new Promise((r) => setTimeout(r, 2000));
                                                }
                                                const out = (await assetsApi.getBuildSummary(
                                                    scope as ScopeUrl,
                                                    job.derived_key,
                                                )) as { collection?: string } | null;
                                                return out?.collection ?? s.staging_id;
                                            })
                                        }
                                    >
                                        Publish
                                    </button>
                                )}
                            </li>
                        ))}
                    </ul>
                </details>
            )}
        </div>
    );
};

export default RequestCollection;
