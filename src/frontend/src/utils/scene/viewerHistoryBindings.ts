/**
 * When the viewer's undo / redo history is forgotten: whenever the models in the scene change
 * (a load, an unload, a clear) or the scope does. An entry points at meshes and range ids of the
 * scene it was recorded in; after either change an undo could act on objects that are gone, or
 * bring back a selection of a model the user has since replaced.
 *
 * Kept apart from utils/scene/viewerHistory.ts so the recording path does not pull the scope
 * store (sessionStorage) into every module that records.
 */

import {useModelState} from "@/state/modelState";
import {useScopeStore} from "@/state/scopeStore";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";

/** Start resetting the history on scene / scope changes. Returns the unsubscribe. */
export function bindViewerHistoryReset(): () => void {
    const reset = () => useViewerHistoryStore.getState().reset();
    const offModels = useModelState.subscribe((s, prev) => {
        if (s.loadedSourceNames !== prev.loadedSourceNames) reset();
    });
    const offScope = useScopeStore.subscribe((s, prev) => {
        const a = s.current;
        const b = prev.current;
        if (a?.kind !== b?.kind || a?.id !== b?.id) reset();
    });
    return () => {
        offModels();
        offScope();
    };
}
