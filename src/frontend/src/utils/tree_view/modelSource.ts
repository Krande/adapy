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
