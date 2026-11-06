// The Scene panel's actions on this window's scene: the loaded models, the groups they
// carry and the selection boxes. `sceneActions` loads this when one runs (and a pop-out's
// request runs the same code in the main window).

import type {TreeNodeData} from "@/components/tree_view/CustomNode";

import type {BoxPatch, GroupKey} from "./sceneActions";
import {notifySceneInfoChanged} from "./sceneInfoSignal";

/** Show or hide a loaded model's scene group (it stays loaded). */
export async function setModelVisible(source: string, visible: boolean): Promise<void> {
    const [{loadedSourceGroups}, {requestRender}] = await Promise.all([import("@/state/modelState"), import("@/state/perfStore")]);
    const group = loadedSourceGroups.get(source);
    if (!group) return;
    group.visible = visible;
    requestRender();
    notifySceneInfoChanged();
}

export async function unloadModels(sources: readonly string[]): Promise<void> {
    const {unload_any_source} = await import("@/utils/scene/handlers/unload_any_source");
    for (const name of sources) {
        try {
            await unload_any_source(name);
        } catch (err) {
            console.error("unload failed", name, err);
        }
    }
}

/** Select the group `key` names among the groups the loaded models carry; null clears. */
export async function selectGroupByKey(key: GroupKey | null): Promise<void> {
    const [{useSceneInfoStore}, {useObjectInfoStore}, {loadedSourceGroups}, {getViewerRuntime}, {applyGroupSelection, sameGroup}] = await Promise.all([
        import("@/state/sceneInfoStore"),
        import("@/state/objectInfoStore"),
        import("@/state/modelState"),
        import("@/state/viewerRuntime"),
        import("@/utils/groups/applyGroupSelection"),
    ]);
    const info = useSceneInfoStore.getState();
    const group = key ? info.availableGroups.find((g) => sameGroup(g, key)) : null;
    if (key && !group) return;
    await applyGroupSelection(group ?? null, {
        setSelectedGroup: info.setSelectedGroup,
        setName: (name) => useObjectInfoStore.getState().setName(name),
        sourceGroup: (source) => loadedSourceGroups.get(source),
        scene: () => getViewerRuntime().scene.current,
    });
}

export async function updateBox(id: string, patch: BoxPatch): Promise<void> {
    const {useBBoxSelectionStore} = await import("@/state/bboxSelectionStore");
    useBBoxSelectionStore.getState().update(id, patch);
}

export async function removeBox(id: string): Promise<void> {
    const {useBBoxSelectionStore} = await import("@/state/bboxSelectionStore");
    useBBoxSelectionStore.getState().remove(id);
}

export async function activateBox(id: string): Promise<void> {
    const [{useBBoxSelectionStore}, {applyBoxSelection}] = await Promise.all([import("@/state/bboxSelectionStore"), import("@/utils/bboxSelection/applyBoxSelection")]);
    useBBoxSelectionStore.getState().setActive(id);
    applyBoxSelection(id);
}

export async function clearBoxes(): Promise<void> {
    const {useBBoxSelectionStore} = await import("@/state/bboxSelectionStore");
    useBBoxSelectionStore.getState().clearAll();
}

export async function boxAroundSelection(): Promise<void> {
    const {addBoxAroundSelection} = await import("@/utils/bboxSelection/applyBoxSelection");
    addBoxAroundSelection();
}

/** Run the related action `key` of the element row `row` (read from the relations index). */
export async function runRelated(row: TreeNodeData, key: string): Promise<void> {
    const [{collectionOfRow, currentScope, relatedOf}, {relatedActions}] = await Promise.all([import("@/utils/systems/systems"), import("@/utils/systems/relatedActions")]);
    const collection = collectionOfRow(row);
    if (!collection) return;
    const related = await relatedOf(currentScope(), collection, row.name);
    const action = related ? relatedActions(row.name, related).find((a) => a.key === key) : undefined;
    if (!action || action.disabled) return;
    try {
        const sentence = await action.run();
        if (sentence) console.info(sentence);
    } catch (err) {
        console.error(err);
    }
}