// Loading published geometry from the Sources tab: per-row load controls (`useAssetLoads`),
// load requests made from elsewhere (`requestRowLoad`), request-then-load chains held across a
// publish, grouping a selection's loads by provider, and the one bulk load a selection makes.

import { useEffect } from "react";
import { create } from "zustand";
import type { AssetView } from "@/assets/assetView";
import { assetSourceName, LoadCancelled, loadPrepared, NothingToDraw, parseDeliveryClaim, prepareNode, type PreparedNode } from "@/assets/delivery";
import { rowFacts, type RowBadge } from "@/assets/rowFacts";
import { beginBulkLoad, endBulkLoad } from "@/utils/scene/loadingView";
import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import { makePluginContextStandalone } from "@/plugins";
import { assetsApi } from "@/services/api/assets";
import { beginOperation, type Operation } from "@/services/operationTracking";
import { providerName } from "@/state/providerNamesStore";
import { useViewerStores } from "@/state/AdaViewerContext";
import { requestRender } from "@/state/perfStore";
import { selectTreeNode } from "@/utils/tree_view/treeNavigation";
import { OWNER, loadedTreeRoot, realDeliveryDeps, refForBadge } from "./delivery";

/** Load / reveal / unload for one row and ONE provider's claim on it. */
export interface AssetLoadControl {
    /** The row it was offered for. */
    rowId: string;
    badge: RowBadge;
    provider: string;
    loaded: boolean;
    root: TreeNodeData | null;
    busy: boolean;
    error: string | null;
    /** The build's answer was "nothing to draw" (the reason), so there is nothing to load. */
    empty: string | null;
    /** What the build said it could not draw, once loaded: members of the node's source that are
     *  not in the model, and why -- the build summary's `warnings`. Empty when it drew them all. */
    notes: readonly string[];
    /** The network half of a load -- the delivery claim, and the build when it needs one -- without
     *  touching the scene. A bulk load starts this for the next nodes while the current one loads.
     *  ``op``: the operation its build job is a step of. */
    prepare: (op?: Operation) => Promise<PreparedNode>;
    /** Resolves when the model is in the scene, or the attempt failed (its error is then in the
     *  store). ``prepared``: a `prepare()` already under way, so it is not started twice. ``op``:
     *  the operation this load is a step of -- held until the model is in, failed with the load. */
    load: (prepared?: Promise<PreparedNode>, op?: Operation) => Promise<void>;
    unload: () => void;
    reveal: () => void;
}

/** Load / reveal / unload for one row, one control per provider with a deliverable claim on it,
 *  driven off the same `RowBadge`s the tree's dots read (`rowFacts().claims`) -- a `solid` or
 *  `ghost` claim is deliverable; `below` is not (the content is further DOWN the tree, so there
 *  is nothing at or above this row to load).
 *
 *  One hook for the detail's bottom row and the row's context menu, so the two can never disagree
 *  about what loading this row means. Load state is keyed per row AND provider: two providers'
 *  geometry for one node are two loads, with two scene names (`assetSourceName` carries the
 *  provider), and either can be on screen without the other.
 *
 *  For several rows (a selection), every row's controls in row order, each scene model ONCE: rows
 *  covered by the same publish above them would otherwise each load the same model. */
export function useAssetLoads(view: AssetView, ids: readonly string[], scope: string): AssetLoadControl[] {
    const { useAssetBrowserStore, useModelState, useTreeViewStore } = useViewerStores();
    const loadBusy = useAssetBrowserStore((s) => s.loadBusy);
    const loadErrors = useAssetBrowserStore((s) => s.loadErrors);
    const loadEmpty = useAssetBrowserStore((s) => s.loadEmpty);
    const loaded = useAssetBrowserStore((s) => s.loaded);
    const liveSourceNames = useModelState((s) => s.loadedSourceNames);
    const treeData = useTreeViewStore((s) => s.treeData);

    // Keep the `loaded` mirror honest against the scene's own truth: a model
    // unloaded from the Files tab (or anywhere else) must stop showing as
    // loaded here on the very next render, not linger until some unrelated
    // asset-browser action happens to touch the store.
    useEffect(() => {
        const store = useAssetBrowserStore.getState();
        // A build's override models go with it: when the build left the scene by any route (the
        // Files tab, the Scene tree), the ones loaded beside it are taken out too.
        for (const a of store.loaded) {
            if (liveSourceNames.has(a.sourceName)) continue;
            for (const name of a.overrides ?? []) {
                if (liveSourceNames.has(name)) makePluginContextStandalone(OWNER).scene.unloadModel(name);
            }
        }
        store.reconcileLoaded(liveSourceNames);
    }, [liveSourceNames, useAssetBrowserStore]);

    const out: AssetLoadControl[] = [];
    const seen = new Set<string>();
    for (const id of ids) for (const badge of rowFacts(view, id)?.claims ?? []) {
        if (badge.weight === "below") continue;
        const ref = refForBadge(view, id, badge);
        if (!ref) continue;
        const key = loadKey(id, badge.provider);
        const sourceName = assetSourceName(ref);
        if (seen.has(sourceName)) continue;
        seen.add(sourceName);
        const asset = loaded.find((a) => a.sourceName === sourceName);
        const loadedHere = !!asset;
        const root = loadedHere ? loadedTreeRoot(treeData, sourceName) : null;
        const deps = (op?: Operation) =>
            realDeliveryDeps((name) => useModelState.getState().loadedSourceNames.has(name), op?.trackJob, op?.signal);
        const prepare = async (op?: Operation): Promise<PreparedNode> => {
            // The claim's own provider in the path: the server then reads THAT provider's
            // manifest for the subject, not whichever provider published it last.
            const wireClaim = await assetsApi.getAssetDelivery(scope, ref.provider, ref.collection, ref.subject, {
                revision: ref.revision,
            });
            return prepareNode(deps(op), scope, ref, parseDeliveryClaim(wireClaim));
        };
        out.push({
            rowId: id,
            badge,
            provider: badge.provider,
            loaded: loadedHere,
            root,
            busy: loadBusy.has(key),
            error: loadErrors.get(key) ?? null,
            empty: loadEmpty.get(key) ?? null,
            notes: asset?.warnings ?? [],
            // The work lives in the store, not in the caller: a context menu closes
            // the moment its item is clicked, and the load must outlive it.
            prepare,
            load: (prepared, given) => {
                useAssetBrowserStore.getState().beginLoad(key);
                // The row's label names the model in the scene; with two providers' geometry for
                // one node side by side, the provider tells the two rows apart.
                const facts = rowFacts(view, id);
                const label = facts?.node.label ?? id;
                const several = (facts?.claims ?? []).filter((b) => b.weight !== "below").length > 1;
                const name = several ? `${label} · ${providerName(badge.provider)}` : label;
                // A load on its own is an operation of its own: its build and its download are two
                // jobs, and the toast follows both as one.
                const op = given ?? beginOperation(`Loading ${name}`);
                const release = op.hold();
                return (async () => {
                    try {
                        op?.stage(`loading ${name}`);
                        const asset = await loadPrepared(deps(op), ref, prepared ?? prepare(op), name);
                        useAssetBrowserStore.getState().endLoad(key, asset);
                        requestRender();
                    } catch (e) {
                        if (e instanceof LoadCancelled) useAssetBrowserStore.getState().cancelLoad(key);
                        else if (e instanceof NothingToDraw) useAssetBrowserStore.getState().emptyLoad(key, e.message);
                        else {
                            const message = e instanceof Error ? e.message : String(e);
                            useAssetBrowserStore.getState().failLoad(key, message);
                            op?.fail(`${name}: ${message}`);
                        }
                    } finally {
                        op.stage(null);
                        release();
                    }
                })();
            },
            unload: () => {
                const scene = makePluginContextStandalone(OWNER).scene;
                scene.unloadModel(sourceName);
                // The build's override models were loaded with it and go with it.
                const gone = new Set([sourceName, ...(asset?.overrides ?? [])]);
                for (const name of gone) if (name !== sourceName) scene.unloadModel(name);
                requestRender();
                // Optimistic: the effect above will re-confirm against
                // `loadedSourceNames` on the next render regardless.
                useAssetBrowserStore.getState().reconcileLoaded(new Set([...liveSourceNames].filter((n) => !gone.has(n))));
            },
            reveal: () => {
                if (root) void selectTreeNode(root);
            },
        });
    }

    // Start the loads an on-demand request was waiting for, once its publish has given the row a
    // claim. `take` makes the start exactly-once across every mounted instance of this hook.
    const pending = usePendingLoads((s) => s.keys);
    useEffect(() => {
        if (!pending.size) return;
        for (const control of out) {
            const k = loadKey(control.rowId, control.provider);
            if (control.loaded && usePendingLoads.getState().take(k)) {
                // Already in the scene: nothing to load, and nothing for its operation to wait for.
                pendingOps.get(k)?.release();
                pendingOps.delete(k);
            }
            if (control.loaded || control.busy) continue;
            const op = pendingOps.get(k);
            if (usePendingLoads.getState().take(k)) {
                pendingOps.delete(k);
                void control.load(undefined, op?.op).finally(() => op?.release());
            }
        }
        // `out` is rebuilt every render; what matters is the claims it carries and the pending set.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pending, out.map((c) => loadKey(c.rowId, c.provider)).join("|")]);

    // Loads asked for from OUTSIDE the tree (`requestRowLoad`: the attribute search's "Load
    // geometry"), once the row is revealed and its controls exist. Only where the choice is not the
    // user's to make: ONE provider with geometry to load. Several, and the row's detail shows them
    // side by side as for any row, and the request is dropped. A row with no controls yet keeps it
    // waiting: a provider asked from the detail (an on-demand request) gives it one when it publishes.
    const rowLoads = useRowLoadRequests((s) => s.rows);
    useEffect(() => {
        if (!rowLoads.size) return;
        for (const id of ids) {
            if (!rowLoads.has(id)) continue;
            const mine = out.filter((c) => c.rowId === id);
            if (!mine.length || !useRowLoadRequests.getState().take(id)) continue;
            const todo = mine.filter((c) => !c.loaded && !c.busy && !c.empty);
            if (todo.length === 1 && mine.length === 1) void todo[0].load();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [rowLoads, ids.join("|"), out.map((c) => loadKey(c.rowId, c.provider)).join("|")]);
    return out;
}

/** Rows whose geometry was asked for from outside the tree, waiting for their load controls. */
const useRowLoadRequests = create<{ rows: ReadonlySet<string>; add: (id: string) => void; take: (id: string) => boolean }>(
    (set, get) => ({
        rows: new Set(),
        add: (id) => set((s) => ({ rows: new Set([...s.rows, id]) })),
        take: (id) => {
            if (!get().rows.has(id)) return false;
            set((s) => ({ rows: new Set([...s.rows].filter((r) => r !== id)) }));
            return true;
        },
    }),
);

/** Load row `id`'s geometry the way its own "Load into scene" would, once the row is selected and
 *  its detail drawn -- call after revealing it (`revealRow`). Starts only when exactly one provider
 *  has geometry to load there; otherwise the detail shows the choice. */
export function requestRowLoad(id: string): void {
    useRowLoadRequests.getState().add(id);
}

/** The store's load-state key: a row and a provider, since each provider's claim loads alone. */
export function loadKey(id: string, provider: string): string {
    return `${id}\u0000${provider}`;
}

/** Loads waiting on an on-demand request: when the request publishes and the tab re-reads, the
 *  row's new claim for that provider is loaded by whichever `useAssetLoads` sees it first.
 *  Module-level rather than component state because the request outlives the menu or detail that
 *  started it, and the claim arrives in a later render than the click. */
const usePendingLoads = create<{
    keys: ReadonlySet<string>;
    add: (keys: readonly string[]) => void;
    drop: (keys: readonly string[]) => void;
    /** Remove `key` and say whether it was there -- so exactly one caller starts the load. */
    take: (key: string) => boolean;
}>((set, get) => ({
    keys: new Set(),
    add: (keys) => set((s) => ({ keys: new Set([...s.keys, ...keys]) })),
    drop: (keys) => set((s) => ({ keys: new Set([...s.keys].filter((k) => !keys.includes(k))) })),
    take: (key) => {
        if (!get().keys.has(key)) return false;
        set((s) => ({ keys: new Set([...s.keys].filter((k) => k !== key)) }));
        return true;
    },
}));

/** The operation each pending load is a step of, held while the load waits for its claim; the load
 *  holds it itself once it starts. */
const pendingOps = new Map<string, { op: Operation; release: () => void }>();

/** How long, after its request finished, a pending load holds its operation up waiting for the
 *  claim -- a row whose claim never comes (its model already loaded, or the publish did not cover
 *  it) must not keep the toast up for ever. The load still starts if the claim comes later. */
const PENDING_HOLD_MS = 30_000;

/** Request-and-load as ONE operation in the toast: the request's jobs, then each load's build and
 *  download, as rows under it. `keys` load once `run` publishes; dropped when it fails. */
function requestThenLoad(label: string, keys: readonly string[], run: (op: Operation) => Promise<boolean>): void {
    requestThenLoadInto(beginOperation(label), keys, run);
}

/** `requestThenLoad` as one part of a larger operation (a set's several providers). */
export function requestThenLoadInto(op: Operation, keys: readonly string[], run: (op: Operation) => Promise<boolean>): void {
    const release = op.hold();
    for (const k of keys) {
        pendingOps.get(k)?.release();
        pendingOps.set(k, { op, release: op.hold() });
    }
    usePendingLoads.getState().add(keys);
    const releaseWaiting = () => {
        for (const k of keys) {
            const held = pendingOps.get(k);
            if (held?.op === op) {
                pendingOps.delete(k);
                held.release();
            }
        }
    };
    // A failed request is reported where every request reports (its error under the detail's request
    // controls, and the operation's); the loads it would have started are dropped with it.
    void run(op)
        .then((done) => {
            if (!done) {
                usePendingLoads.getState().drop(keys);
                releaseWaiting();
            } else {
                setTimeout(releaseWaiting, PENDING_HOLD_MS);
            }
        })
        .finally(release);
}

/** "Load into scene" for a provider that has published nothing at or above these rows yet, but
 *  can be asked for them quickly (`on_demand`). One per such provider. */
export interface RequestLoadControl {
    provider: string;
    busy: boolean;
    blocked: string | null;
    /** Confirm with the user, request, and load what arrives. */
    run: () => void;
}

/** The on-demand request-and-load controls for `ids`: every on-demand provider with no deliverable
 *  claim on any of them. A provider that already has a claim loads it the ordinary way. */
export function requestLoadsFor(
    view: AssetView,
    ids: readonly string[],
    loads: readonly AssetLoadControl[],
    requests: readonly NodeRequestControl[],
): RequestLoadControl[] {
    const claimed = new Set(loads.map((l) => l.provider));
    return requests
        .filter((r) => r.onDemand && !claimed.has(r.provider))
        .map((r) => ({
            provider: r.provider,
            busy: r.busy,
            blocked: r.blocked,
            run: () => {
                const what =
                    ids.length === 1 ? `"${rowFacts(view, ids[0])?.node.label ?? ids[0]}"` : `these ${ids.length} nodes`;
                const ok = window.confirm(
                    `${what} ${ids.length === 1 ? "has" : "have"} no ${providerName(r.provider)} geometry in this scope yet.\n\n` +
                        `Request ${ids.length === 1 ? "it" : "them"} from ${providerName(r.provider)} now? The geometry is fetched once, ` +
                        "published here so it is cached for everyone in the scope, and then loaded into the scene.",
                );
                if (!ok) return;
                requestThenLoad(
                    `Request and load ${what} · ${providerName(r.provider)}`,
                    ids.map((id) => loadKey(id, r.provider)),
                    (op) => r.run(op),
                );
            },
        }));
}

/** Asking the provider for one node's geometry (`asset_node_request`), as the tab tracks it. Null
 *  where the node's provider declares no such request. */
export interface NodeRequestControl {
    /** The provider asked, and the one the result is published under. */
    provider: string;
    label: string;
    busy: boolean;
    stage: string | null;
    error: string | null;
    /** After a request that found its source unchanged: which existing publish still covers it. */
    note: string | null;
    /** Why the request cannot be made by this user, or null. */
    blocked: string | null;
    /** The provider declared the request quick enough to run as part of a load (`on_demand`). */
    onDemand: boolean;
    /** Resolves true when every batch published (or was already up to date) and the tab re-read.
     *  ``op``: the operation the request is a step of -- its jobs are rows under it. */
    run: (op?: Operation) => Promise<boolean>;
}

export const NOTHING_TO_LOAD = "No geometry is published at or above this node yet";
export const NOTHING_TO_LOAD_ANY = "No geometry is published at or above any of the selected nodes yet";

/** A selection's load controls, per provider: what is still to load, what is in the scene, what is
 *  loading and what failed. One reading for the detail's buttons and the menu's items. */
export interface LoadGroup {
    provider: string;
    toLoad: AssetLoadControl[];
    loaded: AssetLoadControl[];
    busy: number;
    failed: AssetLoadControl[];
}

export function loadGroups(controls: readonly AssetLoadControl[]): LoadGroup[] {
    const by = new Map<string, LoadGroup>();
    for (const c of controls) {
        const g = by.get(c.provider) ?? { provider: c.provider, toLoad: [], loaded: [], busy: 0, failed: [] };
        if (c.loaded) g.loaded.push(c);
        else if (c.busy) g.busy += 1;
        else if (!c.empty) g.toLoad.push(c);
        if (!c.busy && c.error) g.failed.push(c);
        by.set(c.provider, g);
    }
    return [...by.values()];
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Load a selection's models as ONE bulk load of the collection: the camera goes to its loading
 *  view (or frames the first model) and stays there, and the models load one at a time -- several
 *  parsed at once was the main thread held for all of them together. A single model is an
 *  ordinary load, fitted as before. */
export async function loadSelection(
    scope: string,
    collection: string,
    controls: readonly AssetLoadControl[],
    given?: Operation,
): Promise<void> {
    if (!controls.length) return;
    // ONE operation in the toast (or part of the caller's): each model's build and download a row
    // under it, the toast up from the first to the last -- one model's too, its build and its
    // download being two jobs with a gap between.
    const op =
        given ??
        beginOperation(controls.length === 1 ? `Loading ${collection}` : `Loading ${plural(controls.length, "model")} · ${collection}`);
    const release = op.hold();
    try {
        await loadEach(scope, collection, controls, op);
    } finally {
        release();
    }
}

async function loadEach(scope: string, collection: string, controls: readonly AssetLoadControl[], op: Operation): Promise<void> {
    if (controls.length < 2) {
        await controls[0]?.load(undefined, op);
        return;
    }
    const session = beginBulkLoad(scope, `assets:${collection}`);
    // THE NEXT NODES ARE PREPARED WHILE ONE LOADS. Preparing a node is network and server work --
    // its delivery claim, and for a node not built yet a build job that can take minutes -- and
    // nothing in it touches the scene, so a few run ahead of the loads, which stay one at a time.
    const PREPARE_AHEAD = 3;
    const prepared = new Map<AssetLoadControl, Promise<PreparedNode>>();
    const prepareAhead = (from: number) => {
        for (let j = from; j < Math.min(controls.length, from + 1 + PREPARE_AHEAD); j++) {
            if (op.signal.aborted) return;
            if (prepared.has(controls[j])) continue;
            const p = controls[j].prepare(op);
            // Settled even if nobody awaits it: its error, if any, surfaces in that node's own load.
            p.catch(() => undefined);
            prepared.set(controls[j], p);
        }
    };
    try {
        for (let i = 0; i < controls.length; i++) {
            // Cancelled from the toast: the rest of the batch is never asked for.
            if (op.signal.aborted) break;
            prepareAhead(i);
            await controls[i].load(prepared.get(controls[i]), op);
            prepared.delete(controls[i]);
        }
    } finally {
        endBulkLoad(session);
    }
}

/** How to ask one provider for geometry for some rows -- `nodeRequestsFor`, narrowed to what the set
 *  load needs. `null` where the provider declares no node request. */
export type RequestMissing = (
    provider: string,
    ids: readonly string[],
) => { blocked: string | null; run: (op?: Operation) => Promise<boolean> } | null;
