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

/** An object of a loaded model found by its name: its model's key and id there (what a pick and a
 *  highlight name it by), its name, and the source its model was loaded from. */
export interface ObjectByName {
    model: string;
    id: string;
    name: string;
    sourceName: string | null;
}

/** The objects of the loaded models called one of `names` (their Scene tree rows), in tree order;
 *  with `sourceName`, only those of the model loaded from that source. A name may match several
 *  objects (names repeat across models, and within one). Walks the tree once. */
export function findObjectsByName(names: Iterable<string>, options: {sourceName?: string} = {}): ObjectByName[] {
    const wanted = new Set(names);
    const out: ObjectByName[] = [];
    if (!wanted.size) return out;
    const tree = useTreeViewStore.getState().treeData;
    const roots = tree?.id === "__roots__" ? tree.children : tree ? [tree] : [];
    for (const root of roots) {
        const sourceName = root.source_name ?? null;
        if (options.sourceName !== undefined && sourceName !== options.sourceName) continue;
        const stack = [root];
        while (stack.length) {
            const node = stack.pop()!;
            const model = node.model_key ?? root.model_key;
            if (wanted.has(node.name) && node.rangeId != null && model) {
                out.push({model, id: String(node.rangeId), name: node.name, sourceName});
            }
            const children = node.children ?? [];
            for (let i = children.length - 1; i >= 0; i--) stack.push(children[i]);
        }
    }
    return out;
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
