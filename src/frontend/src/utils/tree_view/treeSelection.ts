// The selection as tree rows: which rows back the selected draw ranges, the level
// they make up, and the parent level above it. Read from the stores alone (no
// scene lookups), so the window mirror (`@/layout/windowMirrorCore`) can publish
// it without loading scene code; `./treeNavigation` acts on it.

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {useTreeViewStore} from "@/state/treeViewStore";
import {activeViewport} from "@/viewport/api/viewportApi";
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

/** Tree nodes backing the currently-selected objects of the viewport on screen (resolved by the
 *  globally-unique ``model_key|rangeId`` — never by repeating display name). */
export function selectedLeafNodes(): TreeNodeData[] {
    const store = useTreeViewStore.getState();
    const out: TreeNodeData[] = [];
    for (const ref of activeViewport()?.selection() ?? []) {
        const node = store.findNodeByRangeId(ref.model, ref.id);
        if (node) out.push(node);
    }
    return out;
}

/** What the selection is now, by identity: a different value once it has changed. */
const selectionToken = (): unknown => activeViewport()?.selectionToken() ?? null;

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
    noted = {ids: [...ids], sel: selectionToken()};
}

/**
 * The scene-tree rows that are selected: the rows a tree selection chose while
 * its 3D selection stands, else the rows backing the selected ranges (a pick in
 * 3D). The selection store's map is replaced on every change, so a pick, a hide
 * or a clear since the rows were chosen falls back to the ranges.
 */
export function treeSelectionIds(): readonly string[] {
    if (noted && noted.sel === selectionToken()) return noted.ids;
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

/**
 * This window's tree widget shows `ids` selected (scrolled to the last), without
 * reporting a selection of its own; nothing when it shows them already. By
 * default it scrolls only while the tree drawer is open.
 */
export function showTreeSelection(ids: readonly string[], scroll?: boolean): void {
    const tv = useTreeViewStore.getState();
    scroll ??= !tv.isTreeCollapsed;
    const t: any = tv.tree;
    if (!t || !tv.treeData) return;
    const now = new Set<string>((t.selectedNodes ?? []).map((n: {id: string}) => n.id));
    if (now.size === ids.length && ids.every((id) => now.has(id))) return;
    t.isProgrammaticChange = true;
    try {
        const last = ids.length ? ids[ids.length - 1] : null;
        const api = last && typeof t.get === "function" ? t.get(last) : null;
        t.setSelection({ids: [...ids], mostRecent: api, anchor: api});
        if (api && scroll) t.scrollTo({id: last});
    } finally {
        t.isProgrammaticChange = false;
    }
}

/** How many draw ranges are selected. */
export function selectedRangeCount(): number {
    return activeViewport()?.selectionCount() ?? 0;
}
