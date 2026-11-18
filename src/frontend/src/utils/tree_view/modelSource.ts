// Which file a loaded model came from, by the Scene tree's root rows (each carries its model's key
// and the source it was loaded under). No three.js.
import {useTreeViewStore} from "@/state/treeViewStore";

/** The source the model `modelKey` was loaded from; null when the tree lists no such model. */
export function sourceOfModel(modelKey: string | null | undefined): string | null {
    if (!modelKey) return null;
    const tree = useTreeViewStore.getState().treeData;
    const roots = tree?.id === "__roots__" ? tree.children : tree ? [tree] : [];
    for (const root of roots) if (root.model_key === modelKey && root.source_name) return root.source_name;
    return null;
}

/** The name of the object `id` of model `modelKey` (its Scene tree row) and the source that model was
 *  loaded from; null where the tree has no such row or model. What a viewport that knows an object
 *  only by its model and id hands a plugin holding the scene's clicks. */
export function describeObject(modelKey: string, id: string): {name: string | null; sourceName: string | null} {
    return {
        name: useTreeViewStore.getState().findNodeByRangeId(modelKey, id)?.name ?? null,
        sourceName: sourceOfModel(modelKey) ?? null,
    };
}
