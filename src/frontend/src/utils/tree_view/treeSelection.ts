// The selection as tree rows: which rows back the selected draw ranges, the level
// they make up, and the parent level above it. Read from the stores alone (no
// scene lookups), so the window mirror (`@/layout/windowMirrorCore`) can publish
// it without loading scene code; `./treeNavigation` acts on it.

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {buildTreeIndices, lowestCommonAncestor, selectableParent, type TreeIndices} from "./treeGraph";

// --- indices cache (rebuilt only when treeData identity changes) ---------- //

let cachedRoot: TreeNodeData | null = null;
let cachedIndices: TreeIndices | null = null;

/** The loaded tree's parent / by-id indices, rebuilt when the tree changes. */
export function treeIndices(): TreeIndices | null {
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

/** The key a selected mesh's ranges are indexed under in the tree (`findNodeByRangeId`). */
export function meshModelKey(mesh: unknown): string | undefined {
    const m = mesh as {unique_key?: string; userData?: Record<string, unknown>};
    return m.unique_key ?? (m.userData ? (m.userData["unique_hash"] as string | undefined) : undefined);
}

/** Tree nodes backing the currently-selected draw ranges (resolved by the
 *  globally-unique ``model_key|rangeId`` — never by repeating display name). */
export function selectedLeafNodes(): TreeNodeData[] {
    const store = useTreeViewStore.getState();
    const out: TreeNodeData[] = [];
    for (const [mesh, ranges] of useSelectedObjectStore.getState().selectedObjects) {
        const key = meshModelKey(mesh);
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
    const idx = treeIndices();
    if (!idx) return null;
    return lowestCommonAncestor(selectedLeafNodes(), idx);
}

/** Name of the parent level the "up" action would select, or null when there is
 *  none (nothing selected, or already at a file root). For the button tooltip +
 *  disabled state. */
export function parentLevelName(): string | null {
    const idx = treeIndices();
    const cur = currentSelectionNode();
    if (!idx || !cur) return null;
    return selectableParent(cur, idx)?.name ?? null;
}

// --- the selected ROWS ------------------------------------------------------ //

/** The rows a tree selection chose, and the 3D selection it made: valid while that is still the selection. */
let noted: {ids: readonly string[]; sel: unknown} | null = null;

/** Record that the rows `ids` were just selected (a row click, a navigation): call after the 3D selection is set. */
export function noteTreeSelection(ids: readonly string[]): void {
    noted = {ids: [...ids], sel: useSelectedObjectStore.getState().selectedObjects};
}

/**
 * The scene-tree rows that are selected: the rows a tree selection chose while
 * its 3D selection stands, else the rows backing the selected ranges (a pick in
 * 3D). The selection store's map is replaced on every change, so a pick, a hide
 * or a clear since the rows were chosen falls back to the ranges.
 */
export function treeSelectionIds(): readonly string[] {
    const sel = useSelectedObjectStore.getState().selectedObjects;
    if (noted && noted.sel === sel) return noted.ids;
    const out: string[] = [];
    const seen = new Set<string>();
    for (const n of selectedLeafNodes()) {
        if (seen.has(n.id)) continue;
        seen.add(n.id);
        out.push(n.id);
    }
    return out;
}

/** The loaded tree's rows of `ids`, in that order; unknown ids are dropped. */
export function rowsById(ids: readonly string[]): TreeNodeData[] {
    const idx = treeIndices();
    if (!idx) return [];
    const out: TreeNodeData[] = [];
    for (const id of ids) {
        const n = idx.byId.get(id);
        if (n && n !== idx.root) out.push(n);
    }
    return out;
}

/** How many draw ranges are selected. */
export function selectedRangeCount(): number {
    let n = 0;
    for (const ids of useSelectedObjectStore.getState().selectedObjects.values()) n += ids.size;
    return n;
}
