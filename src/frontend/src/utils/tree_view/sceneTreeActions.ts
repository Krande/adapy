/**
 * What the Scene tree's right-click menu does to the rows it was opened on.
 *
 * The rows act on the draw ranges beneath them, read straight from the tree -- not from the 3D
 * selection. The two usually agree (selecting a row selects its ranges), but hiding clears the 3D
 * selection while the rows stay selected, and "Show" must still know what to bring back.
 */

import type {NodeApi} from "react-arborist";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {centerViewOnSelection} from "@/utils/scene/centerViewOnSelection";
import {unhideAllRanges} from "@/utils/scene/visibility";

/** A batched mesh, by what it can do rather than `instanceof`: a hot reload can leave the scene
 *  holding meshes of an earlier copy of the class. */
function isBatched(o: unknown): o is CustomBatchedMesh {
    return !!o && typeof (o as CustomBatchedMesh).getHiddenRanges === "function" && typeof (o as CustomBatchedMesh).hideBatchDrawRange === "function";
}

/** The mesh that draws `n`'s own range, or null for a row with none (a pure level). `cache` saves
 *  the scene-graph lookup across a walk; lookups that find nothing are not cached, since the mesh
 *  may simply not have arrived yet. */
function meshOf(n: TreeNodeData, cache: Map<string, CustomBatchedMesh>): CustomBatchedMesh | null {
    if (n.rangeId == null || !n.node_name || !n.model_key) return null;
    const key = `${n.model_key}\u0000${n.node_name}`;
    const hit = cache.get(key);
    if (hit) return hit;
    const found = getViewerRuntime().modelKeyMap.current?.get(n.model_key)?.getObjectByName(n.node_name);
    // The name can resolve to a wrapper around the mesh rather than the mesh itself (selection
    // entries are keyed either way; see hideSelectedRanges), so look inside it too.
    let mesh: CustomBatchedMesh | null = isBatched(found) ? found : null;
    if (!mesh && found) {
        found.traverse((o) => {
            if (!mesh && isBatched(o)) mesh = o;
        });
    }
    if (!mesh) return null;
    cache.set(key, mesh);
    return mesh;
}

/** The draw ranges under `rows` (each row's whole subtree), grouped by the mesh that draws them. A
 *  row nested under another selected row is counted once. */
export function rangesUnder(rows: readonly TreeNodeData[]): Map<CustomBatchedMesh, Set<string>> {
    const out = new Map<CustomBatchedMesh, Set<string>>();
    const meshes = new Map<string, CustomBatchedMesh>();
    const seen = new Set<string>();
    const stack = [...rows];
    while (stack.length) {
        const n = stack.pop()!;
        if (seen.has(n.id)) continue;
        seen.add(n.id);
        for (const c of n.children ?? []) stack.push(c);
        const mesh = meshOf(n, meshes);
        if (!mesh) continue;
        const ids = out.get(mesh) ?? new Set<string>();
        ids.add(String(n.rangeId));
        out.set(mesh, ids);
    }
    return out;
}

/** How many of the ranges under `rows` are hidden, and how many there are. */
export function hiddenCount(rows: readonly TreeNodeData[]): {hidden: number; total: number} {
    let hidden = 0;
    let total = 0;
    for (const [mesh, ids] of rangesUnder(rows)) {
        const h = mesh.getHiddenRanges();
        total += ids.size;
        for (const id of ids) if (h.has(id)) hidden++;
    }
    return {hidden, total};
}

/** What a row shows about visibility: everything under it drawn, all of it hidden, some of it, or
 *  nothing drawn at all (a level whose geometry never loaded). */
export type RowVisibility = "shown" | "hidden" | "partial" | "none";

// Counts per row for one visibility version. A row's count is its own range plus its children's,
// so the rows on screen share the work below them: a version costs one walk of what they hold.
let countsVersion = -1;
let countsTree: unknown = null;
let counts = new WeakMap<TreeNodeData, {hidden: number; total: number}>();
let countMeshes = new Map<string, CustomBatchedMesh>();

function countsOf(root: TreeNodeData): {hidden: number; total: number} {
    // Post-order without recursion: a deep tree must not cost a stack frame per level.
    const stack: [TreeNodeData, boolean][] = [[root, false]];
    while (stack.length) {
        const [n, expanded] = stack.pop()!;
        if (counts.has(n)) continue;
        if (!expanded) {
            stack.push([n, true]);
            for (const c of n.children ?? []) if (!counts.has(c)) stack.push([c, false]);
            continue;
        }
        let hidden = 0;
        let total = 0;
        const mesh = meshOf(n, countMeshes);
        if (mesh) {
            total = 1;
            if (mesh.getHiddenRanges().has(String(n.rangeId))) hidden = 1;
        }
        for (const c of n.children ?? []) {
            const cc = counts.get(c)!;
            hidden += cc.hidden;
            total += cc.total;
        }
        counts.set(n, {hidden, total});
    }
    return counts.get(root)!;
}

/** `node`'s visibility as of `version` (`useVisibilityStore`) and `tree` (the store's treeData):
 *  pass what the caller subscribed to, so a hide anywhere, or a model arriving, re-reads every row. */
export function rowVisibility(node: TreeNodeData, version: number, tree: unknown): RowVisibility {
    if (version !== countsVersion || tree !== countsTree) {
        countsVersion = version;
        countsTree = tree;
        counts = new WeakMap();
        countMeshes = new Map();
    }
    const {hidden, total} = countsOf(node);
    if (total === 0) return "none";
    if (hidden === 0) return "shown";
    return hidden === total ? "hidden" : "partial";
}

export function hideRows(rows: readonly TreeNodeData[]): void {
    for (const [mesh, ids] of rangesUnder(rows)) mesh.hideBatchDrawRange(ids);
    // As Shift+H: the selection overlay would keep drawing what was just hidden.
    useSelectedObjectStore.getState().clearSelectedObjects();
    useObjectInfoStore.getState().setName(null);
    requestRender();
}

export function showRows(rows: readonly TreeNodeData[]): void {
    for (const [mesh, ids] of rangesUnder(rows)) mesh.unhideBatchDrawRange(ids);
    requestRender();
}

/** Show `rows` and hide everything else in the scene. */
export function isolateRows(rows: readonly TreeNodeData[]): void {
    const scene = getViewerRuntime().scene.current;
    if (!scene) return;
    const keep = rangesUnder(rows);
    scene.traverse((obj) => {
        if (obj instanceof CustomBatchedMesh) obj.isolateDrawRanges(keep.get(obj) ?? new Set());
    });
    requestRender();
}

export function showAll(): void {
    unhideAllRanges();
}

/** Frame the 3D selection -- which, after the right-click, is these rows. */
export function frameSelection(): void {
    const {controls, camera} = getViewerRuntime();
    if (controls.current && camera.current) centerViewOnSelection(controls.current, camera.current);
}

/** Open (or close) `node` and every level below it. */
export function setSubtreeOpen(node: NodeApi<TreeNodeData>, open: boolean): void {
    const stack: NodeApi<TreeNodeData>[] = [node];
    while (stack.length) {
        const n = stack.pop()!;
        if (!n.isInternal) continue;
        if (open) n.open();
        else n.close();
        for (const c of n.children ?? []) stack.push(c);
    }
}
