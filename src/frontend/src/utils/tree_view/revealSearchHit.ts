// An attribute-search hit, shown in the Sources tree.
//
// The drawer is opened on the Sources tab (mounting it, the first time, is what reads the scope's
// collections), the hit's collection is chosen, and the tree is walked down the hit's path one
// level at a time (`walkHitPath`, `@/assets/attributeSearch`) -- the same level fetches expanding a
// row by hand does, through the same loader, so nothing is fetched that browsing there would not.
// The deepest row reached is opened to, selected and scrolled to (`revealRow`).
//
// `load`: then load that row's geometry the way its own "Load into scene" does (`requestRowLoad`).
// Only when the walk reached the hit itself -- loading an ancestor would be a different, possibly
// much larger, model than the one asked for.

import {hitTreePath, treeCollectionFor, walkHitPath} from "@/assets/attributeSearch";
import {ancestorsOf} from "@/assets/hierarchy";
import {requestRowLoad} from "@/components/asset_browser/AssetsTab";
import type {SearchHit} from "@/services/api/attributeSearch";
import {assetsApi} from "@/services/api/assets";
import {sourceNodesApi} from "@/services/api/sourceNodes";
import {getSingletonViewerStores} from "@/state/AdaViewerContext";
import type {AssetBrowserState} from "@/state/assetBrowserStore";
import {loaderFor} from "@/state/assetBrowserLoader";
import {scopeUrlPart} from "@/state/scopeStore";

import {sourcesTree} from "./revealPick";

export type RevealOutcome =
    | {status: "exact"; row: string}
    | {status: "ancestor"; row: string}
    | {status: "not-found"; message: string}
    /** A newer reveal started before this one finished. */
    | {status: "superseded"};

/** Bumped per reveal: a walk still fetching levels for an older click gives up. */
let generation = 0;

/** Resolve once `pred` holds for the store's state, or reject after `ms`. */
function waitFor(
    store: {getState(): AssetBrowserState; subscribe(l: (s: AssetBrowserState) => void): () => void},
    pred: (s: AssetBrowserState) => boolean,
    ms = 30_000,
): Promise<void> {
    if (pred(store.getState())) return Promise.resolve();
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            unsubscribe();
            reject(new Error("the Sources tree did not finish loading"));
        }, ms);
        const unsubscribe = store.subscribe((s) => {
            if (!pred(s)) return;
            clearTimeout(timer);
            unsubscribe();
            resolve();
        });
    });
}

export async function revealSearchHit(hit: SearchHit, opts: {load?: boolean} = {}): Promise<RevealOutcome> {
    const gen = ++generation;
    const alive = () => gen === generation;
    const stores = getSingletonViewerStores();
    const store = stores.useAssetBrowserStore;
    const scope = scopeUrlPart(stores.useScopeStore.getState().current);

    // Open the drawer on Sources; the tab loads the scope's collections when it first mounts.
    stores.useTreeViewStore.getState().setIsTreeCollapsed(false);
    store.getState().setTab("assets");
    await waitFor(store, (s) => s.scope === scope && s.collections !== null);
    if (!alive()) return {status: "superseded"};

    const collection = treeCollectionFor(store.getState().collections, hit.collection);
    if (!collection) {
        return {status: "not-found", message: `No tree is published for collection ${hit.collection} in this scope`};
    }
    const loader = loaderFor(store, assetsApi, sourceNodesApi);
    if (store.getState().collection !== collection) {
        await loader.chooseCollection(scope, collection);
    } else {
        await waitFor(store, (s) => !!s.index && !s.indexLoading);
    }
    if (!alive()) return {status: "superseded"};

    const tree = sourcesTree(store, loader, scope, alive);
    const reached = await walkHitPath(tree, hitTreePath(hit));
    if (!alive()) return {status: "superseded"};
    if (!reached) {
        return {status: "not-found", message: `${hit.full_name} is not in the published tree of ${collection}`};
    }
    const open = [...ancestorsOf(tree.hierarchy(), reached.row), ...reached.chain.slice(0, -1)];
    store.getState().revealRow(reached.row, open);
    if (!reached.exact) return {status: "ancestor", row: reached.row};
    if (opts.load) requestRowLoad(reached.row);
    return {status: "exact", row: reached.row};
}
