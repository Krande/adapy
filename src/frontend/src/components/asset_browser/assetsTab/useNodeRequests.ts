// What the Sources tab may ask providers for, read off the live worker specs: which
// providers take a request for ONE node (`asset_node_request`), which declare per-collection
// request options, and the per-node request state behind a row's "Request" action.

import { useEffect, useMemo, useState } from "react";

import type { AssetView } from "@/assets/assetView";
import { nodeBatches, requestNodes, type NodeTarget } from "@/assets/collectionRequest";
import { sourcesTabAccess } from "@/assets/sourcesTabAccess";
import { assetProviderCollections, type AssetNodeRequest, type AssetRequestOptions } from "@/services/assetScopeCollections";
import { viewerApi } from "@/services/viewerApi";

import { requestDeps } from "../RequestCollection";
import { loadKey, type NodeRequestControl } from "./loads";

/** The providers that take a node request, and those that declare request options for `collection`. */
export function useProviderRequestSpecs(
    scope: string,
    collection: string | null,
): { nodeRequests: ReadonlyMap<string, AssetNodeRequest>; collectionOptionProviders: ReadonlyMap<string, AssetRequestOptions> } {
    // Which providers can be asked for ONE node (`asset_node_request`), read off the live specs.
    const [nodeRequests, setNodeRequests] = useState<ReadonlyMap<string, AssetNodeRequest>>(new Map());
    // Which providers declare per-collection request options, and the collections they serve.
    const [optionProviders, setOptionProviders] = useState<
        ReadonlyArray<{ providerId: string; collections: readonly string[]; declared: AssetRequestOptions }>
    >([]);
    useEffect(() => {
        let live = true;
        viewerApi
            .listBackendPlugins()
            .then((res) => {
                if (!live) return;
                const byProvider = new Map<string, AssetNodeRequest>();
                const withOptions: { providerId: string; collections: readonly string[]; declared: AssetRequestOptions }[] = [];
                for (const p of assetProviderCollections(res.plugins ?? [])) {
                    if (p.nodeRequest) byProvider.set(p.providerId, p.nodeRequest);
                    if (p.requestOptions) withOptions.push({ providerId: p.providerId, collections: p.collections, declared: p.requestOptions });
                }
                setNodeRequests(byProvider);
                setOptionProviders(withOptions);
            })
            .catch(() => {
                // No specs, no request offered: the tree still browses.
            });
        return () => {
            live = false;
        };
    }, [scope]);
    // The providers that declare request options for THIS collection -- matched as the provider
    // spells its collections, which need not be the key's lower case.
    const collectionOptionProviders = useMemo(() => {
        const out = new Map<string, AssetRequestOptions>();
        const key = (collection ?? "").toLowerCase();
        for (const p of optionProviders) {
            if (p.collections.some((c) => c.toLowerCase() === key)) out.set(p.providerId, p.declared);
        }
        return out;
    }, [optionProviders, collection]);
    return { nodeRequests, collectionOptionProviders };
}

/**
 * The request controls for some rows: one per provider that takes a node request, with the
 * progress of each (row, provider) request kept here. `refresh` re-reads the scope's publishes
 * once every batch of a request is done.
 */
export function useNodeRequests(
    view: AssetView | null,
    scope: string,
    isAdmin: boolean,
    nodeRequests: ReadonlyMap<string, AssetNodeRequest>,
    refresh: () => Promise<unknown>,
): (ids: readonly string[]) => NodeRequestControl[] {
    // Per node, so a request keeps its progress while the user looks at other rows. Both jobs are
    // in the toast as well, and a request that outlives the tab is under "Staged, not published".
    const [nodeRequestState, setNodeRequestState] = useState<
        ReadonlyMap<string, { busy: boolean; stage: string | null; error: string | null; note: string | null }>
    >(new Map());
    const patchNodeRequest = (key: string, patch: Partial<{ busy: boolean; stage: string | null; error: string | null; note: string | null }>) =>
        setNodeRequestState((cur) => {
            const next = new Map(cur);
            next.set(key, { ...(cur.get(key) ?? { busy: false, stage: null, error: null, note: null }), ...patch });
            return next;
        });
    // EVERY provider that can be asked for one node, not only the one whose spine drew the row:
    // the point of a second provider is geometry the first does not have. Each request publishes
    // under the provider that was asked, so its claim sits beside the others rather than on top.
    //
    // SEVERAL NODES (a selection) go in as few requests as the provider takes: batches of its
    // declared `maxNodes`, one job each, all started together. The state stays per (node,
    // provider), so each row still shows its own request; the control sums them.
    return (ids: readonly string[]): NodeRequestControl[] => {
        // Asking a provider's workers is an admin's: a member reads what the sweeps keep current.
        if (!view || !sourcesTabAccess(isAdmin).requests) return [];
        const targets: NodeTarget[] = [];
        for (const id of ids) {
            const node = view.hierarchy.byId.get(id)?.data;
            if (node) targets.push({ id, label: node.label });
        }
        if (!targets.length) return [];
        const collection = view.collection;
        const many = targets.length > 1;
        return [...nodeRequests.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([providerId, req]) => {
                const states = targets.map((t) => nodeRequestState.get(loadKey(t.id, providerId)));
                const running = states.filter((st) => st?.busy);
                const errors = targets.flatMap((t, i) => (states[i]?.error && !states[i]?.busy ? [`${t.label ?? t.id}: ${states[i]!.error}`] : []));
                const notes = states.flatMap((st) => (st?.note && !st.busy ? [st.note] : []));
                return {
                    provider: providerId,
                    label: many ? `${req.label} (${targets.length})` : req.label,
                    busy: running.length > 0,
                    stage: many ? (running.length ? `${running.length} of ${targets.length} running` : null) : (states[0]?.stage ?? null),
                    error: errors.length ? (many ? `${errors.length} failed:\n${errors.join("\n")}` : states[0]!.error) : null,
                    note: notes.length ? (many ? `${notes.length} of ${targets.length} answered without a new publish` : notes[0]) : null,
                    blocked: req.requiresAdmin && !isAdmin ? `Only an administrator can run ${req.pluginId}` : null,
                    onDemand: !!req.onDemand,
                    run: async (op) => {
                        const results = await Promise.all(
                            nodeBatches(req, targets).map((batch) => {
                                const keys = batch.map((t) => loadKey(t.id, providerId));
                                const patch = (p: Parameters<typeof patchNodeRequest>[1]) => keys.forEach((k) => patchNodeRequest(k, p));
                                patch({ busy: true, stage: null, error: null, note: null });
                                return requestNodes(requestDeps((stage) => patch({ stage }), op?.trackJob), scope, providerId, req, collection, batch)
                                    .then((out) => {
                                        // Nothing: the provider's source does not carry these nodes. Said as a
                                        // note, not an error, and false so no load waits on a publish that
                                        // will not come.
                                        if (out.nothing) {
                                            patch({ busy: false, stage: null, note: `nothing to load: ${out.message}` });
                                            return false;
                                        }
                                        // Unchanged: the provider's last publish already covers these nodes, so
                                        // nothing new was published -- the note says which one.
                                        patch({ busy: false, stage: null, note: out.unchanged ? `up to date (${out.revision})` : null });
                                        return true;
                                    })
                                    .catch((e) => {
                                        const message = e instanceof Error ? e.message : String(e);
                                        patch({ busy: false, stage: null, error: message });
                                        op?.fail(message);
                                        return false;
                                    });
                            }),
                        );
                        // Re-read, so the new publish's claims reach the rows and Load enables -- once,
                        // after every batch, rather than once per batch.
                        await refresh();
                        return results.every(Boolean);
                    },
                };
            });
    };
}
