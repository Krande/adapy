// What the panels do for a viewport that builds no three.js scene: list its model in the Scene tree, and
// answer its clicks as the three.js click handler answers its own (the row revealed, the object named).
// The viewport only announces (viewport/api: a model loaded, a click picked); this side holds the
// panels' code, which the viewport's own chunk must not import.
import {cacheAndBuildTree} from "@/state/model_worker/cacheModelUtils";
import {useModelState} from "@/state/modelState";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {query_ws_server_mesh_info} from "@/utils/mesh_select/handlers/send_mesh_selected_info_callback";
import {revealPickInTrees} from "@/utils/tree_view/revealPick";
import {onModelLoaded, onViewportPick, type LoadedModelInfo, type ViewportPick} from "@/viewport/api/viewportApi";

function listModel(info: LoadedModelInfo): void {
    let extras: Record<string, unknown> = {};
    try {
        if (info.sceneExtras) extras = JSON.parse(info.sceneExtras) as Record<string, unknown>;
    } catch (err) {
        console.warn("[viewport] the model's scene extras are not JSON; its tree is empty", err);
    }
    // A model that replaced what the viewport showed starts the tree over; one that joined adds a root.
    if (!info.added) useTreeViewStore.getState().clearTreeData();
    void cacheAndBuildTree(info.model, extras, info.sourceName);
}

function showPick(pick: ViewportPick): void {
    const info = useObjectInfoStore.getState();
    info.setClickedFace(null);
    if (!pick.ref) {
        info.setName(null);
        info.setSelectedNodeId(null);
        return;
    }
    revealPickInTrees(pick.ref.model, pick.ref.id);
    const node = useTreeViewStore.getState().findNodeByRangeId(pick.ref.model, pick.ref.id);
    if (!node?.name) return;
    info.setName(node.name);
    info.setSelectedNodeId(node.id);
    const activeFile = useModelState.getState().loadedSourceName;
    info.setFileName(activeFile);
    info.setJsonData(null);
    void query_ws_server_mesh_info(node.name, 0, activeFile);
}

let installed = false;

/** Start listening; once per page. */
export function installViewportPanelBridge(): void {
    if (installed) return;
    installed = true;
    onModelLoaded(listModel);
    onViewportPick(showPick);
}
