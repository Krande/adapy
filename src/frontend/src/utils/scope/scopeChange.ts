import {type ScopeOption, useScopeStore} from "@/state/scopeStore";
import {useServerInfoStore} from "@/state/serverInfoStore";
import {useModelState} from "@/state/modelState";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {
    fileListRequestedFor,
    fileListScopeKey,
    request_list_of_files_from_server,
} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {confirmDialog} from "@/plugins/dialogs";

/** Switch scope. Tears down what belongs to the scope being left: the file list (the
 *  request below fills it again), every loaded model, and the selection. */
export function applyScopeChange(picked: ScopeOption): void {
    void clear_loaded_model();
    useScopeStore.getState().setCurrent(picked);
    // Unless subscribeFileListToScope's listener has just done it.
    relistUnlessOn(picked);
}

/** Empty the file list and ask for the current scope's, unless that is the scope the
 *  last request was for. */
function relistUnlessOn(scope: ScopeOption | null): void {
    if (fileListRequestedFor() === fileListScopeKey(scope)) return;
    useServerInfoStore.getState().setServerFileObjects([]);
    useServerInfoStore.getState().setServerFiles([]);
    void request_list_of_files_from_server();
}

/** Keep the Storage list on the current scope however the scope changes: a page or a deep
 *  link that sets it directly (not through `applyScopeChange`) would otherwise leave
 *  Storage listing the old scope's files while loads go to the new one. Compared with the
 *  scope the last request was sent for, so a change made before this subscribes (the
 *  deep link can apply its scope first) is caught too. */
export function subscribeFileListToScope(): () => void {
    if (fileListRequestedFor() !== null) relistUnlessOn(useScopeStore.getState().current);
    return useScopeStore.subscribe((state) => {
        if (fileListRequestedFor() !== null) relistUnlessOn(state.current);
    });
}

/** Is there anything in the scene a scope switch would discard. */
export function sceneHasModels(): boolean {
    return (
        useModelState.getState().loadedSourceNames.size > 0 ||
        (getViewerRuntime().modelKeyMap.current?.size ?? 0) > 0 ||
        useCellBuilderStore.getState().active !== null
    );
}

/** `applyScopeChange`, after asking when it would discard loaded models. Resolves true
 *  when the scope changed. */
export async function requestScopeChange(picked: ScopeOption): Promise<boolean> {
    if (sceneHasModels() && !await confirmDialog({
        title: `Switch to ${picked.name}?`,
        body: "Every loaded model is unloaded: the models belong to the scope you are leaving.",
        confirmLabel: "Switch scope",
        tone: "danger",
    })) return false;
    applyScopeChange(picked);
    return true;
}
