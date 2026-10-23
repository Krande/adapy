/**
 * What ties the viewer's undo / redo history to the rest of the app:
 *
 *  - a scope change forgets it: its entries name models and boxes of the scope just left;
 *  - models loaded and removed become entries (utils/scene/modelHistory.ts), reloaded through the
 *    page-restore paths (utils/scene/sceneRestore.ts). A model change no longer forgets the
 *    history: entries name models by scene name, so they work again once an undo brings a removed
 *    model back. A page restore starts the history clean itself.
 *
 * Kept apart from utils/scene/viewerHistory.ts so the recording path does not pull the scope
 * store (sessionStorage) and the loaders into every module that records.
 */

import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useGalleryStore} from "@/state/galleryStore";
import {useScopeStore} from "@/state/scopeStore";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {bindModelHistory} from "@/utils/scene/modelHistory";
import {describeLoadedModel, reloadRecordedModel, sceneRestoreRunning} from "@/utils/scene/sceneRestore";

let bound = 0;
let unbindAll: (() => void) | null = null;

/** Bind the history to scope changes and model loads. Returns the unbinding; binding again while
 *  bound (a second viewer) shares the one binding. */
export function bindViewerHistoryReset(): () => void {
    bound += 1;
    if (!unbindAll) {
        const reset = () => useViewerHistoryStore.getState().reset();
        const offScope = useScopeStore.subscribe((s, prev) => {
            const a = s.current;
            const b = prev.current;
            if (a?.kind !== b?.kind || a?.id !== b?.id) reset();
        });
        const offModels = bindModelHistory({
            describe: describeLoadedModel,
            load: reloadRecordedModel,
            unload: async (name) => {
                const {unload_any_source} = await import("@/utils/scene/handlers/unload_any_source");
                await unload_any_source(name);
            },
            shouldRecord: () =>
                !sceneRestoreRunning() && !useGalleryStore.getState().enabled && useCellBuilderStore.getState().active === null,
        });
        unbindAll = () => {
            offScope();
            offModels();
        };
    }
    let done = false;
    return () => {
        if (done) return;
        done = true;
        bound -= 1;
        if (bound === 0 && unbindAll) {
            unbindAll();
            unbindAll = null;
        }
    };
}
