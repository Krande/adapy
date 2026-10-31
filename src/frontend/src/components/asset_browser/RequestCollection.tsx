// "Request from a provider": ask a provider for one of its collections, and
// publish it into the provider store, from the Assets tab.
//
// AN ADMIN'S OPERATION. Every provider job runs in the provider store, and keeping provider data
// current is the scheduled jobs' work: an admin is offered every collection the live workers
// advertise (only a provider whose spec declares `asset_collection_request` appears). A member is
// offered no request at all: they read what the sweeps keep current, as their scope's grants
// (Admin ▸ Providers ▸ Access) allow.
//
// THE FLOW is `@/assets/collectionRequest`: the provider's job stages, core
// publishes. Both jobs go to the toast, so a request can outlive this panel;
// one that finishes unwatched is under "Staged, not published" below, from
// `GET /assets/staging`, where it can be published by hand.

import React, { useCallback, useEffect, useMemo, useState } from "react";

import { pickOptions, type CollectionPicks } from "@/assets/collectionChoices";
import { requestCollection, type CollectionRequestDeps } from "@/assets/collectionRequest";
import CollectionChoicesEditor from "@/components/admin/CollectionChoicesEditor";
import { providerIdTitle } from "@/assets/providerNames";
import { requestValues } from "@/assets/providerOptions";
import { PROVIDER_STORE_SCOPE } from "@/assets/providerStore";
import { readProviderOptions } from "@/services/providerOptions";
import { makePluginContextStandalone } from "@/plugins";
import { assetsApi } from "@/services/api/assets";
import type { ScopeUrl } from "@/services/api/client";
import { conversionApi } from "@/services/api/conversion";
import { assetProviderCollections, type AssetProviderCollections } from "@/services/assetScopeCollections";
import { COLLECTION_SPLITS_KEY, parseCollectionSplits } from "@/services/collectionSplits";
import { viewerApi } from "@/services/viewerApi";
import { useMeStore } from "@/state/meStore";
import { useProviderName } from "@/state/providerNamesStore";

import AssetFiles, { DeleteButton, formatSize } from "./AssetFiles";

const OWNER = "assets";

type Staged = Awaited<ReturnType<typeof assetsApi.listStaging>>["staged"][number] & {
    /** Where it is staged: the open scope's, or -- for an admin -- the provider store's. */
    scope: string;
};

/** The live api for `@/assets/collectionRequest`, jobs tracked in the toast -- as their own entries,
 *  or as steps of an operation (`trackJob`, an `Operation`'s). Shared with the tree's per-node
 *  request. */
export function requestDeps(
    onStage: (s: string) => void,
    trackJob?: CollectionRequestDeps["trackJob"],
): CollectionRequestDeps {
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
        trackJob:
            trackJob ??
            ((opts) => {
                makePluginContextStandalone(OWNER).trackJob(opts);
            }),
        onStage,
        // Read fresh per request, so a value someone just changed is the one sent. Only what the
        // panel stored, which only ever stores the options the provider declares.
        providerOptions: async (_scope, providerId, collection) => {
            const doc = await readProviderOptions(collection);
            return requestValues(doc, providerId, Object.keys(doc.providers[providerId] ?? {}));
        },
    };
}

/** `deps` whose provider options also carry the admin's per-collection picks (`{option: value}`),
 *  so the request's job reads the chosen source instead of the worker's own choice. */
export function withChoices(deps: CollectionRequestDeps, picked: Record<string, unknown>): CollectionRequestDeps {
    if (!Object.keys(picked).length) return deps;
    const base = deps.providerOptions;
    return {
        ...deps,
        providerOptions: async (scope, providerId, collection) => ({
            ...(base ? await base(scope, providerId, collection) : {}),
            ...picked,
        }),
    };
}

const RequestCollection: React.FC<{
    scope: string;
    /** Called with the collection a request published, so the tab can show it. */
    onPublished: (collection: string) => void;
    /** Called after a file or revision was deleted, so the tab re-reads what is published. */
    onChanged?: () => void;
    /** A section drawn directly below the request (and its progress), above staging and Files. */
    belowRequest?: React.ReactNode;
}> = ({ scope, onPublished, onChanged, belowRequest }) => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const pn = useProviderName();
    const [providers, setProviders] = useState<AssetProviderCollections[] | null>(null);
    const [staged, setStaged] = useState<Staged[]>([]);
    const [providerId, setProviderId] = useState("");
    const [collection, setCollection] = useState("");
    const [busy, setBusy] = useState(false);
    const [stage, setStage] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState<string | null>(null);
    // The admin's per-collection picks for this request (a share, say); none: the worker decides.
    const [picks, setPicks] = useState<CollectionPicks>({});

    const load = useCallback(async () => {
        // A provider job stages into the provider store, whichever scope asked, so an admin's
        // unpublished requests are listed from there as well; a member has none to see.
        const stagedIn = async (s: string): Promise<Staged[]> =>
            (await assetsApi.listStaging(s as ScopeUrl).catch(() => ({ staged: [] }))).staged.map((x) => ({ ...x, scope: s }));
        const [plugins, rawSplits, ownStaged, storeStaged] = await Promise.all([
            viewerApi.listBackendPlugins().catch(() => ({ plugins: [] })),
            viewerApi.getPublicSetting(COLLECTION_SPLITS_KEY).catch(() => null),
            stagedIn(scope),
            isAdmin && scope !== PROVIDER_STORE_SCOPE ? stagedIn(PROVIDER_STORE_SCOPE) : Promise.resolve([] as Staged[]),
        ]);
        // A split collection is requested as one of its copies, as the server offers it.
        const requestable = assetProviderCollections(plugins.plugins ?? [], parseCollectionSplits(rawSplits)).filter(
            (p) => p.request,
        );
        setProviders(requestable);
        setStaged([...ownStaged, ...storeStaged]);
        setProviderId((cur) => (requestable.some((p) => p.providerId === cur) ? cur : requestable[0]?.providerId ?? ""));
    }, [scope, isAdmin]);

    useEffect(() => {
        setProviders(null);
        void load();
    }, [load]);

    const provider = providers?.find((p) => p.providerId === providerId) ?? null;
    const offered = useMemo(() => (provider ? [...provider.collections] : []), [provider]);
    useEffect(() => {
        if (!offered.includes(collection)) setCollection(offered[0] ?? "");
    }, [offered, collection]);

    const run = useCallback(
        async (what: () => Promise<string | { collection: string; note: string }>) => {
            setBusy(true);
            setError(null);
            setDone(null);
            try {
                const result = await what();
                // A provider that found its source unchanged published nothing -- say so, in its words.
                const published = typeof result === "string" ? result : result.collection;
                setDone(typeof result === "string" ? `Published ${published}.` : result.note);
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

    if (providers === null) {
        return (
            <div className="px-2 py-2 space-y-1.5 border-b border-gray-700/70 text-xs">
                <div className="text-gray-400">Reading providers…</div>
                {belowRequest}
            </div>
        );
    }

    const request = provider?.request ?? null;
    // Provider jobs are an admin's: a member reads what the scheduled sweeps keep current, through
    // their scope's grants, and is offered nothing to request here.
    const blockedForUser = !isAdmin;

    return (
        <div className="px-2 py-2 space-y-1.5 border-b border-gray-700/70 text-xs">
            {provider && request && !blockedForUser && (
                // Folded by default, as Files is: a request is made now and then, the tree is used
                // all the time. What a request is doing (stage, error, result) shows outside it.
                <details>
                    <summary className="cursor-pointer text-gray-400 select-none">
                        Request from a provider
                        <span className="ml-1 text-gray-500" title={providerIdTitle(provider.providerId)}>
                            · {pn(provider.providerId)}
                            {collection ? ` / ${collection}` : ""}
                        </span>
                    </summary>
                    <div className="mt-1.5 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-1">
                        {/* Shown even for ONE provider: the request goes to that provider, and a
                            row naming no provider read as if the choice depended on another one
                            being up (a single provider looked like no provider at all). */}
                        {providers.length > 0 && (
                            <select
                                aria-label="Provider"
                                className="h-7 rounded-md border border-gray-700 bg-gray-800 text-xs text-gray-100 px-2"
                                value={providerId}
                                disabled={busy}
                                onChange={(e) => setProviderId(e.target.value)}
                            >
                                {providers.map((p) => (
                                    <option key={p.providerId} value={p.providerId} title={providerIdTitle(p.providerId)}>
                                        {pn(p.providerId)}
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
                                        withChoices(requestDeps(setStage), pickOptions(request.collectionChoices ?? [], picks, collection)),
                                        scope,
                                        provider.providerId,
                                        request,
                                        collection,
                                    );
                                    return out.unchanged
                                        ? { collection: out.collection, note: `Up to date: ${out.message ?? "nothing changed"}.` }
                                        : out.collection;
                                })
                            }
                        >
                            {busy ? "Working…" : request.label}
                        </button>
                    </div>
                    {!!collection && !!request.collectionChoices?.length && (
                        <CollectionChoicesEditor
                            decls={request.collectionChoices}
                            collections={[collection]}
                            picks={picks}
                            onChange={setPicks}
                            disabled={busy}
                        />
                    )}
                    {provider.collections.length === 0 && (
                        <div className="text-gray-500">No online worker advertises this provider's collections right now.</div>
                    )}
                    </div>
                </details>
            )}
            {stage && <div className="text-gray-300">{stage}…</div>}
            {error && <div className="text-red-300 break-words">{error}</div>}
            {done && <div className="text-green-300">{done}</div>}
            {belowRequest}

            {staged.length > 0 && (
                <details>
                    <summary className="cursor-pointer text-gray-400">
                        Staged, not published ({staged.length} · {formatSize(staged.reduce((n, s) => n + s.size, 0))})
                    </summary>
                    <ul className="mt-1 space-y-1">
                        {staged.map((s) => (
                            <li key={`${s.scope}/${s.staging_id}`}>
                            <details>
                            <summary className="flex items-center gap-2 cursor-pointer rounded px-1 hover:bg-white/5">
                                <code className="truncate text-gray-300" title={s.files.map((f) => f.key).join("\n")}>
                                    {s.staging_id}
                                </code>
                                <span className="text-gray-500 truncate">
                                    {s.files.length} file{s.files.length === 1 ? "" : "s"} · {formatSize(s.size)}
                                </span>
                                {providers.length > 0 && isAdmin && (
                                    <button
                                        type="button"
                                        className="ml-auto h-6 px-2 rounded-md border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50"
                                        disabled={busy}
                                        title={`Publish as ${pn(providerId)}\n${providerIdTitle(providerId)}`}
                                        onClick={() =>
                                            void run(async () => {
                                                setStage(`publishing ${s.staging_id}`);
                                                const job = await assetsApi.publishStaged(s.scope as ScopeUrl, {
                                                    provider: providerId,
                                                    staging_id: s.staging_id,
                                                });
                                                const ranIn = job.scope ?? s.scope;
                                                makePluginContextStandalone(OWNER).trackJob({
                                                    jobId: job.job_id,
                                                    label: `Publish ${s.staging_id}`,
                                                    derivedKey: job.derived_key,
                                                    scope: ranIn,
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
                                                    ranIn as ScopeUrl,
                                                    job.derived_key,
                                                )) as { collection?: string } | null;
                                                return out?.collection ?? s.staging_id;
                                            })
                                        }
                                    >
                                        Publish
                                    </button>
                                )}
                            </summary>
                            <ul className="pl-3">
                                {s.files.map((f) => (
                                    <li key={f.key} className="flex items-center gap-2 px-1 rounded hover:bg-white/5">
                                        <span className="truncate text-gray-300" title={f.key}>
                                            {f.file}
                                        </span>
                                        <span className="ml-auto shrink-0 font-mono text-[11px] text-gray-500">{formatSize(f.size)}</span>
                                        <DeleteButton
                                            title="Delete this staged file"
                                            disabled={busy}
                                            onClick={() => {
                                                if (!window.confirm(`Delete ${f.key}?\n\nIt is staged, not published: nothing references it.`)) return;
                                                void (async () => {
                                                    try {
                                                        await assetsApi.deleteFile(s.scope as ScopeUrl, f.key);
                                                    } catch (e) {
                                                        setError(e instanceof Error ? e.message : String(e));
                                                    }
                                                    await load();
                                                })();
                                            }}
                                        />
                                    </li>
                                ))}
                            </ul>
                            </details>
                            </li>
                        ))}
                    </ul>
                </details>
            )}
            <AssetFiles scope={scope} onChanged={onChanged ?? (() => {})} />
        </div>
    );
};

export default RequestCollection;
