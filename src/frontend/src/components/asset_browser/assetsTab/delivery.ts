// How the Sources tab reaches the scene and the asset REST routes for a load: the real
// `LoadNodeDeps`, the node reference a row's badge loads, and the Files-tab tree root of a
// source that is already loaded.

import type { AssetView } from "@/assets/assetView";
import type { LoadNodeDeps, NodeRef } from "@/assets/delivery";
import type { RowBadge } from "@/assets/rowFacts";
import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import { makePluginContextStandalone } from "@/plugins";
import { assetsApi } from "@/services/api/assets";
import { authHeader, type ScopeUrl } from "@/services/api/client";
import { conversionApi } from "@/services/api/conversion";
import { filesApi } from "@/services/api/files";
import { useModelSessionStore } from "@/state/modelSession";
import { getViewerRuntime } from "@/state/viewerRuntime";

// Owner tag for every scene object this tab adds. A standalone plugin context
// is the documented way for CORE UI (not just a plugin) to
// reach `SceneHandle.loadModelFromUrl`/`unloadModel`, so loading and unloading
// go through the exact path a plugin would use rather than a second one.
export const OWNER = "assets";

/** `state/model_worker/cacheModelUtils.ts`'s synthetic container id -- every
 *  loaded model's tree root is one of its children (or the tree root itself,
 *  when only one model is loaded). Mirrored here rather than imported because
 *  that module exports no constant, only the behaviour. */
const ROOTS_CONTAINER_ID = "__roots__";

/** Real `LoadNodeDeps` for `loadNode`: REST calls through `assetsApi` /
 *  `conversionApi` (the same job-status route a plugin job polls), the scene
 *  through a standalone plugin context, and `useModelState.loadedSourceNames`
 *  for "is this already loaded". Built fresh per call rather than memoised --
 *  every field it closes over is either a stable module export or a snapshot
 *  read at call time, so there is nothing to keep in sync. A load that is one step of an operation
 *  passes its `trackJob`, so the build jobs are rows under it, and its `signal`, so cancelling the
 *  operation stops the load. */
export function realDeliveryDeps(
    isLoaded: (sourceName: string) => boolean,
    trackJob?: LoadNodeDeps["trackJob"],
    signal?: AbortSignal,
): LoadNodeDeps {
    return {
        ...(signal ? {signal} : {}),
        api: {
            buildAssetNode: (scope, body) => assetsApi.buildAssetNode(scope, body),
            getBuildSummary: (scope, key) => assetsApi.getBuildSummary(scope, key),
            async jobStatus(jobId) {
                const status = await conversionApi.convertStatus(jobId);
                return { status: status.status, error: status.error };
            },
            getAssetDelivery: (scope, provider, collection, subject, opts) =>
                assetsApi.getAssetDelivery(scope as ScopeUrl, provider, collection, subject, opts),
        },
        loadModelFromUrl: (owner, url, opts) => makePluginContextStandalone(OWNER).scene.loadModelFromUrl(owner, url, opts),
        isLoaded,
        blobUrl: (scope, key) => filesApi.blobUrl(scope, key),
        blobHeaders: () => authHeader(),
        trackJob:
            trackJob ??
            ((opts) => {
                makePluginContextStandalone(OWNER).trackJob(opts);
            }),
    };
}

/** The `NodeRef` a Load click on row `id` targets: `badge.at` (the manifest-
 *  owning subject -- the clicked row itself for a `solid` badge, the covering
 *  ancestor for `ghost`/`below`) carries the request; `id` rides along as
 *  `node` only when it differs, so two different covered rows stay separately
 *  revealable (`assetSourceName`) even though both load the same content. */
export function refForBadge(view: AssetView, id: string, badge: RowBadge): NodeRef | null {
    const owner = view.hierarchy.byId.get(badge.at)?.data;
    if (!owner) return null;
    return {
        // The claim's provider -- its manifest's -- not the owner row's: the row names whichever
        // provider's spine merged last, and would send a load to the wrong publish.
        provider: badge.provider || owner.provider,
        collection: view.collection,
        subject: badge.at,
        revision: badge.revision,
        node: badge.at === id ? undefined : id,
    };
}

/** The Files-tab tree root for an already-loaded source, if the tree has been built at all.
 *
 * TWO NAMES, AND THEY ARE NOT THE SAME NAME. A load registers its group under the SOURCE NAME
 * (`registerLoadedSource`), while `cacheAndBuildTree` stamps the tree root's `model_key` with the
 * runtime MODEL KEY -- `<scene name>_<uuid>`, minted inside the loader. Matching the root by
 * source name therefore never matched anything, and the reveal action sat permanently disabled
 * beside a model that was plainly on screen.
 *
 * The bridge between the two is the group object itself, which is exactly how the unload path
 * resolves the same question (`unload_source_from_scene`): find the group registered under this
 * source name, then find the key it is stored under in the runtime's model-key map. */
export function loadedTreeRoot(treeData: TreeNodeData | null, sourceName: string): TreeNodeData | null {
    if (!treeData) return null;
    const group = useModelSessionStore.getState().current()?.groups.get(sourceName);
    if (!group) return null;
    let modelKey: string | null = null;
    getViewerRuntime().modelKeyMap.current?.forEach((g, key) => {
        if (g === group) modelKey = key;
    });
    if (modelKey === null) return null;
    const candidates = treeData.id === ROOTS_CONTAINER_ID ? treeData.children : [treeData];
    return candidates.find((c) => c.model_key === modelKey) ?? null;
}
