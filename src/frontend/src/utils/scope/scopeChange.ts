import {type ScopeOption, useScopeStore} from "@/state/scopeStore";
import {useServerInfoStore} from "@/state/serverInfoStore";
import {useModelState} from "@/state/modelState";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {confirmDialog} from "@/plugins/dialogs";

/** Switch scope. Tears down what belongs to the scope being left: the file list (the
 *  request below fills it again), every loaded model, and the selection. */
export function applyScopeChange(picked: ScopeOption): void {
    useServerInfoStore.getState().setServerFileObjects([]);
    useServerInfoStore.getState().setServerFiles([]);
    void clear_loaded_model();
    useScopeStore.getState().setCurrent(picked);
    void request_list_of_files_from_server();
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
