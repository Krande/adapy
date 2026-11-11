// What the panels do for a viewport that builds no three.js scene: list its model in the Scene tree, and
// answer its clicks as the three.js click handler answers its own (the row revealed, the object named).
// The viewport only announces (viewport/api: a model loaded, a click picked); this side holds the
// panels' code, which the viewport's own chunk must not import.
import {cacheAndBuildTree} from "@/state/model_worker/cacheModelUtils";
import {useModelState} from "@/state/modelState";
import {useLineageStore} from "@/state/lineageStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {clearSourceExtensions, setSourceExtension} from "@/state/sourceExtensions";
import {useTreeViewStore} from "@/state/treeViewStore";
import {query_ws_server_mesh_info} from "@/utils/mesh_select/handlers/send_mesh_selected_info_callback";
import {registerLineageFromExtension} from "@/utils/lineage/registerLineageFromExtension";
import {revealPickInTrees} from "@/utils/tree_view/revealPick";
import {announcedExtension} from "@/viewport/api/modelExtension";
import {onModelLoaded, onViewportPick, type LoadedModelInfo, type ViewportPick} from "@/viewport/api/viewportApi";

function listModel(info: LoadedModelInfo): void {
    let extras: Record<string, unknown> = info.extras ?? {};
    try {
        if (info.sceneExtras) extras = JSON.parse(info.sceneExtras) as Record<string, unknown>;
    } catch (err) {
        console.warn("[viewport] the model's scene extras are not JSON; its tree is empty", err);
    }
    // A model that replaced what the viewport showed starts the tree over; one that joined adds a root.
    if (!info.added) useTreeViewStore.getState().clearTreeData();
    void cacheAndBuildTree(info.model, extras, info.sourceName);
    keepExtension(info);
}

/** What the model's `ADA_EXT_data` extension says beside the geometry, where the panels look for it:
 *  the analysis concepts (state/sourceExtensions.ts) and the link between a design model and the
 *  analysis models made from it (the lineage store). */
function keepExtension(info: LoadedModelInfo): void {
    if (!info.sourceName || info.adaExtData === undefined) return;
    const extension = announcedExtension(info);
    if (!info.added) clearSourceExtensions();
    setSourceExtension(info.sourceName, extension);
    useLineageStore.getState().unregister(info.sourceName);
    if (!extension) return;
    // The ids of a large group are in a buffer view, copied now: the reader is good for this call only.
    const read = info.bufferView;
    const bufferView = (index: number) => read?.(index)?.slice() ?? null;
    void registerLineageFromExtension({bufferView, extension, fileName: info.sourceName, root: null}).catch((err) =>
        console.warn("lineage: register failed for", info.sourceName, err),
    );
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
    // The file the picked model was loaded from (what links it to the models derived from the
    // same design), not whichever file was loaded last.
    const roots = useTreeViewStore.getState().treeData;
    const root = (roots?.id === "__roots__" ? roots.children : roots ? [roots] : []).find((r) => r.model_key === pick.ref!.model);
    const activeFile = root?.source_name ?? useModelState.getState().loadedSourceName;
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
