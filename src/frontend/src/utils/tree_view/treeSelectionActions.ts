// Selecting scene-tree ROWS (by their data, not the tree widget's nodes): what a
// row click does, shared by the scene tree in this window and by a pop-out
// window's mirrored tree (`@/layout/sceneActions`).

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {recordViewerAction} from "@/utils/scene/viewerHistory";

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

/** The draw ranges of `nodes`: a row's `node_name` names its mesh in its model; a row without one is a level. */
function meshesAndRanges(nodes: readonly TreeNodeData[]): [CustomBatchedMesh, string][] {
    const out: [CustomBatchedMesh, string][] = [];
    const models = getViewerRuntime().modelKeyMap.current;
    for (const n of nodes) {
        const scene = n.model_key ? models?.get(n.model_key) : undefined;
        if (!scene || !n.node_name) continue;
        const mesh = scene.getObjectByName(n.node_name) as CustomBatchedMesh | undefined;
        if (mesh) out.push([mesh, n.rangeId as string]);
    }
    return out;
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
    const selectedObjectStore = useSelectedObjectStore.getState();
    // Scope tree search to the selected node's subtree (cleared when nothing
    // is selected, so search spans all roots again).
    useTreeViewStore.getState().setScope(target.length ? target[0].id : null, target.length ? (target[0].name ?? null) : null);
    if (target.length) {
        const batch = meshesAndRanges(subtreeOf(target));
        // One tree selection, one undo entry (Ctrl+Z / Ctrl+Y), however large the subtree.
        recordViewerAction("select", () => {
            selectedObjectStore.clearSelectedObjects();
            selectedObjectStore.addBatchofMeshes(batch);
        });
        // The selected row itself, never a descendant from the subtree walk (selecting a container
        // must name the container). Under multi-select the first row stands for the rest: the
        // widget reports its selection in document order, so there is no "clicked one".
        useObjectInfoStore.getState().setName(target[0].name);
        useObjectInfoStore.getState().setSelectedNodeId(target[0].id);
    } else {
        recordViewerAction("clear-selection", () => selectedObjectStore.clearSelectedObjects());
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
    const {camera, controls} = getViewerRuntime();
    if (!camera.current || !controls.current) return;
    const m = await import("@/components/viewer/sceneHelpers/setupCameraControlsHandlers");
    m.fitSelectionView(camera.current, controls.current);
    requestRender();
}
