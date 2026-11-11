// Selecting scene-tree ROWS (by their data, not the tree widget's nodes): what a
// row click does, shared by the scene tree in this window and by a pop-out
// window's mirrored tree (`@/layout/sceneActions`).

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {activeViewport, type ObjectRef} from "@/viewport/api/viewportApi";

import {noteTreeSelection, rowsById, showTreeSelection, treeIndices, treeSelectionIds} from "./treeSelection";
import {selectTreeNode} from "./treeNavigation";

/** `rows` and every row below them, pre-order, each once. */
function subtreeOf(rows: readonly TreeNodeData[]): TreeNodeData[] {
    const out: TreeNodeData[] = [];
    const seen = new Set<string>();
    const stack = [...rows].reverse();
    while (stack.length) {
        const n = stack.pop()!;
        if (seen.has(n.id)) continue;
        seen.add(n.id);
        out.push(n);
        const kids = n.children ?? [];
        for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
    }
    return out;
}

/** The objects of `nodes`, as the viewport knows them: a row's model, its id in it and the GLB node
 *  that draws it. A row that is only a level names nothing drawn, and the viewport leaves it out. One
 *  ref is yielded again and again with each row's values (the viewport reads it and moves on), so
 *  selecting a model of tens of thousands of objects makes no garbage per object. */
function* objectRefs(nodes: readonly TreeNodeData[]): Generator<ObjectRef> {
    const ref: ObjectRef = {model: "", id: "", node: null};
    for (const n of nodes) {
        if (!n.model_key || n.rangeId == null) continue;
        ref.model = n.model_key;
        ref.id = typeof n.rangeId === "string" ? n.rangeId : String(n.rangeId);
        ref.node = n.node_name ?? null;
        yield ref;
    }
}

/** Combine `rows` with the selected rows by `mode`. */
function combine(rows: readonly TreeNodeData[], mode: "replace" | "add" | "toggle"): TreeNodeData[] {
    if (mode === "replace") return [...rows];
    const current = rowsById(treeSelectionIds());
    const ids = new Set(rows.map((r) => r.id));
    if (mode === "add") return [...current.filter((r) => !ids.has(r.id)), ...rows];
    const have = new Set(current.map((r) => r.id));
    return [...current.filter((r) => !ids.has(r.id)), ...rows.filter((r) => !have.has(r.id))];
}

/**
 * Select `rows` (each one's subtree) in 3D, as a scene-tree click does: one undo
 * entry, the object info naming the first row, the tree search scoped to it.
 * No rows: the selection is cleared. `syncTree` also shows the result in this
 * window's tree widget (for a selection made elsewhere).
 */
export async function selectRows(
    rows: readonly TreeNodeData[],
    mode: "replace" | "add" | "toggle" = "replace",
    opts: {syncTree?: boolean} = {},
): Promise<void> {
    const target = combine(rows, mode);
    const viewport = activeViewport();
    // Scope tree search to the selected node's subtree (cleared when nothing
    // is selected, so search spans all roots again).
    useTreeViewStore.getState().setScope(target.length ? target[0].id : null, target.length ? (target[0].name ?? null) : null);
    if (target.length) {
        // One tree selection, one undo entry (Ctrl+Z / Ctrl+Y), however large the subtree; on whichever
        // viewport is on screen.
        viewport?.setSelection(objectRefs(subtreeOf(target)));
        // The selected row itself, never a descendant from the subtree walk (selecting a container
        // must name the container). Under multi-select the first row stands for the rest: the
        // widget reports its selection in document order, so there is no "clicked one".
        useObjectInfoStore.getState().setName(target[0].name);
        useObjectInfoStore.getState().setSelectedNodeId(target[0].id);
    } else {
        viewport?.setSelection([]);
        useObjectInfoStore.getState().setSelectedNodeId(null);
    }
    const ids = target.map((r) => r.id);
    noteTreeSelection(ids);
    if (opts.syncTree) showTreeSelection(ids);
    requestRender();
}

/** Select tree node `id` (its subtree), as the object info's navigation does. */
export async function selectNodeById(id: string): Promise<void> {
    const node = treeIndices()?.byId.get(id);
    if (node) await selectTreeNode(node);
}

/** Select `rows` and frame them (Shift+F on them). */
export async function fitRows(rows: readonly TreeNodeData[]): Promise<void> {
    await selectRows(rows, "replace", {syncTree: true});
    activeViewport()?.fitSelection();
    requestRender();
}
