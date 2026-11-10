/**
 * What the Scene tree's right-click menu does to the rows it was opened on.
 *
 * The rows act on the draw ranges beneath them, read straight from the tree -- not from the 3D
 * selection. The two usually agree (selecting a row selects its ranges), but hiding clears the 3D
 * selection while the rows stay selected, and "Show" must still know what to bring back.
 */

import type {NodeApi} from "react-arborist";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {batchedMeshOf} from "@/utils/scene/meshLookup";
import {activeViewport, type ObjectRef, type ObjectVisibilityReader} from "@/viewport/api/viewportApi";

/** The mesh that draws `n`'s own range, or null for a row with none (a pure level). */
function meshOf(n: TreeNodeData, cache: Map<string, CustomBatchedMesh>): CustomBatchedMesh | null {
    if (n.rangeId == null || !n.node_name || !n.model_key) return null;
    return batchedMeshOf(n.model_key, n.node_name, cache);
}

/** The objects under `rows` (each row's whole subtree), as the viewport knows them. A row nested
 *  under another selected row is counted once. */
export function refsUnder(rows: readonly TreeNodeData[]): ObjectRef[] {
    const out: ObjectRef[] = [];
    const seen = new Set<string>();
    const stack = [...rows];
    while (stack.length) {
        const n = stack.pop()!;
        if (seen.has(n.id)) continue;
        seen.add(n.id);
        for (const c of n.children ?? []) stack.push(c);
        if (n.model_key && n.rangeId != null) out.push({model: n.model_key, id: String(n.rangeId), node: n.node_name});
    }
    return out;
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

/** How many of the objects under `rows` are hidden, and how many are drawn at all: on the viewport on screen. */
export function hiddenCount(rows: readonly TreeNodeData[]): {hidden: number; total: number} {
    let hidden = 0;
    let total = 0;
    const read = activeViewport()?.objectVisibility();
    if (!read) return {hidden, total};
    for (const ref of refsUnder(rows)) {
        const v = read(ref.model, ref.id, ref.node);
        if (!v) continue;
        total++;
        if (v === "hidden") hidden++;
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
const NOTHING_DRAWN: ObjectVisibilityReader = () => null;
let countReader: ObjectVisibilityReader = NOTHING_DRAWN;
let countViewport: unknown = null;

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
        const v = n.model_key && n.rangeId != null ? countReader(n.model_key, String(n.rangeId), n.node_name) : null;
        if (v) {
            total = 1;
            if (v === "hidden") hidden = 1;
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
    const viewport = activeViewport();
    if (version !== countsVersion || tree !== countsTree || viewport !== countViewport) {
        countsVersion = version;
        countsTree = tree;
        countViewport = viewport;
        counts = new WeakMap();
        countReader = viewport?.objectVisibility() ?? NOTHING_DRAWN;
    }
    const {hidden, total} = countsOf(node);
    if (total === 0) return "none";
    if (hidden === 0) return "shown";
    return hidden === total ? "hidden" : "partial";
}

const VISIBILITY_CODE: Record<RowVisibility, number> = {shown: 0, hidden: 1, partial: 2, none: 3};

/**
 * Every row's visibility under `container` (the store's treeData; the container
 * itself is not a row), as codes, the rows that are shown left out: what the
 * window mirror sends a pop-out's tree (`@/layout/sceneTreeMirror`). One
 * post-order walk with counts and meshes of its own, so it neither reads nor
 * resets the cache the tree's rows use.
 */
export function rowVisibilityMap(container: TreeNodeData): Map<string, number> {
    const out = new Map<string, number>();
    const read = activeViewport()?.objectVisibility() ?? NOTHING_DRAWN;
    const own = new Map<TreeNodeData, {hidden: number; total: number}>();
    const stack: [TreeNodeData, boolean][] = (container.children ?? []).map((c) => [c, false]);
    while (stack.length) {
        const [n, expanded] = stack.pop()!;
        if (!expanded) {
            stack.push([n, true]);
            for (const c of n.children ?? []) stack.push([c, false]);
            continue;
        }
        let hidden = 0;
        let total = 0;
        const state = n.model_key && n.rangeId != null ? read(n.model_key, String(n.rangeId), n.node_name) : null;
        if (state) {
            total = 1;
            if (state === "hidden") hidden = 1;
        }
        for (const c of n.children ?? []) {
            const cc = own.get(c);
            if (!cc) continue;
            hidden += cc.hidden;
            total += cc.total;
        }
        own.set(n, {hidden, total});
        const v: RowVisibility = total === 0 ? "none" : hidden === 0 ? "shown" : hidden === total ? "hidden" : "partial";
        if (v !== "shown") out.set(n.id, VISIBILITY_CODE[v]);
    }
    return out;
}

// Each of these is one undo / redo entry (Ctrl+Z / Ctrl+Y), however many ranges the rows hold.

export function hideRows(rows: readonly TreeNodeData[]): void {
    activeViewport()?.hide(refsUnder(rows));
    useObjectInfoStore.getState().setName(null);
    requestRender();
}

export function showRows(rows: readonly TreeNodeData[]): void {
    activeViewport()?.show(refsUnder(rows));
    requestRender();
}

/** Show `rows` and hide everything else in the scene. */
export function isolateRows(rows: readonly TreeNodeData[]): void {
    activeViewport()?.isolate(refsUnder(rows));
    requestRender();
}

export function showAll(): void {
    activeViewport()?.showAll();
}

/** Frame the 3D selection -- which, after the right-click, is these rows. */
export function frameSelection(): void {
    activeViewport()?.fitSelection();
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
