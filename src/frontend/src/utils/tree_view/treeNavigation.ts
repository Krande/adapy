// Tree-level navigation from the current selection: climb to the parent tree
// level, descend to a child, or move between siblings — driven from the
// selection-tree structure without needing the tree panel to be open.
//
// The "current level" is derived from the live selection: the lowest common
// ancestor (LCA) of the tree nodes backing the selected draw ranges. Selecting
// a leaf makes the leaf the current node; selecting its parent then makes the
// selection span all of the parent's ranges, whose LCA is that parent — so
// repeated "up" calls climb one level at a time with no extra state to track.

import {TreeNodeData} from "@/components/tree_view/CustomNode";
import {modelKeyMapRef} from "@/state/refs";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {buildTreeIndices, lowestCommonAncestor, selectableParent, TreeIndices} from "./treeGraph";

// --- indices cache (rebuilt only when treeData identity changes) ---------- //

let cachedRoot: TreeNodeData | null = null;
let cachedIndices: TreeIndices | null = null;

function indices(): TreeIndices | null {
    const root = useTreeViewStore.getState().treeData;
    if (!root) {
        cachedRoot = null;
        cachedIndices = null;
        return null;
    }
    if (root !== cachedRoot || !cachedIndices) {
        cachedIndices = buildTreeIndices(root);
        cachedRoot = root;
    }
    return cachedIndices;
}

// --- selection <-> tree bridging ------------------------------------------ //

/** Tree nodes backing the currently-selected draw ranges (resolved by the
 *  globally-unique ``model_key|rangeId`` — never by repeating display name). */
function selectedLeafNodes(): TreeNodeData[] {
    const store = useTreeViewStore.getState();
    const out: TreeNodeData[] = [];
    for (const [mesh, ranges] of useSelectedObjectStore.getState().selectedObjects) {
        const key: string | undefined =
            (mesh as any).unique_key ?? (mesh.userData ? mesh.userData["unique_hash"] : undefined);
        if (!key) continue;
        for (const rid of ranges) {
            const node = store.findNodeByRangeId(key, rid);
            if (node) out.push(node);
        }
    }
    return out;
}

/** The tree node representing the current selection level (LCA of the selected
 *  leaves), or null when nothing resolvable is selected. */
export function currentSelectionNode(): TreeNodeData | null {
    const idx = indices();
    if (!idx) return null;
    return lowestCommonAncestor(selectedLeafNodes(), idx);
}

/** Name of the parent level the "up" action would select, or null when there is
 *  none (nothing selected, or already at a file root). For the button tooltip +
 *  disabled state. */
export function parentLevelName(): string | null {
    const idx = indices();
    const cur = currentSelectionNode();
    if (!idx || !cur) return null;
    return selectableParent(cur, idx)?.name ?? null;
}

function collectSubtree(node: TreeNodeData, out: TreeNodeData[]): void {
    out.push(node);
    for (const child of node.children ?? []) collectSubtree(child, out);
}

async function meshesAndRanges(nodes: TreeNodeData[]): Promise<[CustomBatchedMesh, string][]> {
    const res: [CustomBatchedMesh, string][] = [];
    for (const n of nodes) {
        if (n.rangeId == null || !n.node_name || !n.model_key) continue;
        const scene = modelKeyMapRef.current?.get(n.model_key);
        if (!scene) continue;
        const mesh = scene.getObjectByName(n.node_name) as CustomBatchedMesh | undefined;
        if (!mesh) continue;
        res.push([mesh, n.rangeId]);
    }
    return res;
}

/** Select ``node``'s whole subtree in 3D and make it the selection: covers every
 *  draw range beneath it, sets the info-panel name to the node, and — only when
 *  the tree panel is already open — highlights + scrolls to the row. Never opens
 *  the tree. */
export async function selectTreeNode(node: TreeNodeData): Promise<void> {
    const nodes: TreeNodeData[] = [];
    collectSubtree(node, nodes);
    const batch = await meshesAndRanges(nodes);

    const sel = useSelectedObjectStore.getState();
    sel.clearSelectedObjects();
    sel.addBatchofMeshes(batch);

    useObjectInfoStore.getState().setName(node.name);
    useObjectInfoStore.getState().setSelectedNodeId(node.id);
    const tv = useTreeViewStore.getState();
    tv.setScope(node.id, node.name);

    // Mirror the picking path's tree-highlight sync, but only when the tree is
    // already visible — the request is explicit that navigation must not force
    // the tree open.
    if (tv.tree && tv.treeData && !tv.isTreeCollapsed) {
        const t: any = tv.tree;
        t.isProgrammaticChange = true;
        const api = typeof t.get === "function" ? t.get(node.id) : null;
        t.setSelection({ids: [node.id], mostRecent: api, anchor: api});
        if (api) t.scrollTo({id: node.id});
        t.isProgrammaticChange = false;
    }
}

// --- navigation actions (return the new level's name, or null on no-op) --- //

/** Select the parent of the current selection level (one level up the tree). */
export async function selectParentLevel(): Promise<string | null> {
    const idx = indices();
    const cur = currentSelectionNode();
    if (!idx || !cur) return null;
    const par = selectableParent(cur, idx);
    if (!par) return null;
    await selectTreeNode(par);
    return par.name;
}

/** Select the first child of the current selection level (one level down). */
export async function selectChildLevel(): Promise<string | null> {
    const cur = currentSelectionNode();
    const child = cur?.children?.[0];
    if (!child) return null;
    await selectTreeNode(child);
    return child.name;
}

/** Select the previous (-1) or next (+1) sibling of the current selection. */
export async function selectSibling(direction: -1 | 1): Promise<string | null> {
    const idx = indices();
    const cur = currentSelectionNode();
    if (!idx || !cur) return null;
    const par = idx.parent.get(cur.id);
    const sibs = par?.children ?? [];
    const i = sibs.findIndex((s) => s.id === cur.id);
    if (i < 0) return null;
    const j = i + direction;
    if (j < 0 || j >= sibs.length) return null;
    await selectTreeNode(sibs[j]);
    return sibs[j].name;
}
