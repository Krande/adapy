// "Clear" / "Remove all": everything out of the scene, the bookkeeping with it. One function for the
// storage browser's Clear and the Scene tab's Remove all, so the two cannot drift.
//
// `clear_loaded_model` is the teardown (models, tree, selection, FEA session); on top of it:
//
// * loads still QUEUED are dropped -- otherwise they arrive into the scene just cleared;
// * an open procedural model is closed: its proxies are part of what is in the scene;
// * the Sources tab's `loaded` mirror is emptied now, not when that tab next renders -- its
//   "loaded" marks and a box request's "already in the scene" read it.
//
// The scene-restore record follows the scene on its own (`sceneRestore`), so it empties too.

import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {getSingletonViewerStores} from "@/state/AdaViewerContext";
import {useCellBuilderStore} from "@/state/cellBuilderStore";

export async function clearScene(): Promise<void> {
    const {useLoadQueueStore} = await import("@/state/loadQueueStore");
    const queue = useLoadQueueStore.getState();
    for (const t of queue.queued) queue.removeQueued(t.name);

    await clear_loaded_model();

    const cb = useCellBuilderStore.getState();
    if (cb.active) cb.close();

    getSingletonViewerStores().useAssetBrowserStore.getState().reconcileLoaded(new Set());
}
