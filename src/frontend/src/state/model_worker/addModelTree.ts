// A loaded model's root in the Scene tree. No worker here: the worker's tree build is handed in, so
// the order below runs under `node --test`.
//
// ONE TREE AT A TIME. A bulk load (a saved group's members, each loaded alone) puts one model in the
// scene after another without waiting for their trees, and the viewport without a three.js scene
// starts each model's tree without awaiting it. Each build used to read the store once, before its
// worker round-trip, and write the container from that read afterwards: a model whose tree was built
// meanwhile was dropped from the tree (its objects then had no row -- no name, no source -- for the
// trees, the Selected Object panel, or a plugin holding the scene's clicks), and both builds numbered
// their rows from the same first id. Builds now queue, and each reads the store when it is its turn
// and again after its build.
import {useTreeViewStore} from "../treeViewStore";
import {TreeNodeData} from "@/components/tree_view/CustomNode";
import {labelRoots, topLevelName} from "@/utils/tree_view/rootLabels";

// Synthetic container holding one root per loaded model. Its children are the
// per-model roots (labelled by GLB filename); it is never rendered itself —
// TreeViewComponent renders ``treeData.children``.
const ROOTS_CONTAINER_ID = "__roots__";

function filenameLabel(sourceName: string | undefined, fallback: string): string {
    if (!sourceName) return fallback || "model";
    const base = sourceName.split("/").pop() || sourceName;
    return base || fallback || "model";
}

let queue: Promise<void> = Promise.resolve();

/** Build model `key`'s tree (`build`, handed the first free node id) and add it as a root, after
 *  every build asked for before it. */
export function addModelTree(
    key: string,
    build: (startId: number) => Promise<TreeNodeData | null>,
    sourceName?: string,
    displayName?: string,
): Promise<void> {
    const run = queue.then(() => buildAndAdd(key, build, sourceName, displayName));
    queue = run.catch(() => undefined);
    return run;
}

async function buildAndAdd(
    key: string,
    build: (startId: number) => Promise<TreeNodeData | null>,
    sourceName?: string,
    displayName?: string,
): Promise<void> {
    let treeData: TreeNodeData | null;
    try {
        // Numbered from the ids of every model added before it.
        treeData = await build(useTreeViewStore.getState().max_id + 1);
    } catch (err: unknown) {
        console.error("Failed to build tree hierarchy", err);
        return;
    }
    if (!treeData) return;

    // One root per loaded model, labelled by GLB filename (deduped with -2/-3 on collision), held
    // under a synthetic container whose children TreeViewComponent renders as the top level. The
    // store as it is NOW: not as it was before the build.
    const tree_store = useTreeViewStore.getState();
    treeData.model_key = key;
    treeData.source_name = sourceName ?? null;

    const prev = tree_store.treeData;
    const isContainer = !!prev && prev.id === ROOTS_CONTAINER_ID;
    // Existing roots, minus any prior load of this same model (reload replaces).
    const siblings = isContainer
        ? prev!.children.filter((c) => c.model_key !== key)
        : prev
            ? [prev].filter((c) => c.model_key !== key)
            : [];

    // Both names on the root; which one shows is the viewer's choice (`rootLabels`).
    treeData.source_label = filenameLabel(sourceName, treeData.name);
    treeData.top_name = topLevelName(displayName, treeData.name, treeData.source_label);

    const container: TreeNodeData = {
        id: ROOTS_CONTAINER_ID,
        name: "",
        children: [...siblings, treeData],
        model_key: null,
        node_name: null,
    };
    tree_store.setTreeData(labelRoots(container, tree_store.rootLabelMode));

    // Only the NEW model's ids can raise the maximum -- they were numbered from it -- so only
    // its subtree is walked. Walking the whole container (every model loaded so far, an await
    // per node) made each load of a bulk load slower than the one before.
    tree_store.setMaxId(Math.max(useTreeViewStore.getState().max_id, maxNodeId(treeData)) + 1);
}

/** The largest id anywhere under `root`, iteratively: a deep tree must not cost a stack frame (or a
 *  promise) per node. EVERY node, not only leaves -- ids are handed out in `id_hierarchy` order,
 *  so an assembly row can hold the largest one, and the next model numbered from a leaf maximum
 *  reused it: two rows with one id, and a pick selecting the wrong one. */
export function maxNodeId(root: TreeNodeData): number {
    let max = 0;
    const stack: TreeNodeData[] = [root];
    for (let node = stack.pop(); node; node = stack.pop()) {
        const n = Number(node.id);
        if (Number.isFinite(n)) max = Math.max(max, n);
        for (const child of node.children) stack.push(child);
    }
    return max;
}
