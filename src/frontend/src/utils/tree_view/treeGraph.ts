// Pure tree-graph helpers for selection-tree navigation: parent/by-id indexing,
// lowest-common-ancestor, and the "selectable parent" rule. No store, scene or
// three.js imports (the TreeNodeData import is type-only, erased at runtime) so
// this stays unit-testable in the node harness.

import type {TreeNodeData} from "@/components/tree_view/CustomNode";

export interface TreeIndices {
    /** child node id -> its parent node (absent for the synthetic root). */
    parent: Map<string, TreeNodeData>;
    /** node id -> node. */
    byId: Map<string, TreeNodeData>;
    /** The synthetic container root (its children are the per-model roots). */
    root: TreeNodeData;
}

/** Build the parent + by-id indices for a tree rooted at ``root``. */
export function buildTreeIndices(root: TreeNodeData): TreeIndices {
    const parent = new Map<string, TreeNodeData>();
    const byId = new Map<string, TreeNodeData>();
    const stack: Array<[TreeNodeData, TreeNodeData | null]> = [[root, null]];
    while (stack.length) {
        const [node, par] = stack.pop()!;
        byId.set(node.id, node);
        if (par) parent.set(node.id, par);
        for (const child of node.children ?? []) stack.push([child, node]);
    }
    return {parent, byId, root};
}

/** The ancestor chain of ``node``, leaf-first (``node`` itself first, up to and
 *  including the root). */
export function ancestorChain(node: TreeNodeData, idx: TreeIndices): TreeNodeData[] {
    const chain: TreeNodeData[] = [];
    let cur: TreeNodeData | undefined = node;
    while (cur) {
        chain.push(cur);
        cur = idx.parent.get(cur.id);
    }
    return chain;
}

/** Lowest common ancestor of ``nodes`` (the deepest node that is an ancestor of
 *  — or equal to — every input). null when ``nodes`` is empty. */
export function lowestCommonAncestor(nodes: TreeNodeData[], idx: TreeIndices): TreeNodeData | null {
    if (nodes.length === 0) return null;
    // Candidate list = node[0]'s chain (leaf-first); keep only those present in
    // every other node's chain; the first survivor is the deepest = the LCA.
    let common = ancestorChain(nodes[0], idx);
    for (let i = 1; i < nodes.length && common.length; i++) {
        const ids = new Set(ancestorChain(nodes[i], idx).map((n) => n.id));
        common = common.filter((n) => ids.has(n.id));
    }
    return common.length ? common[0] : null;
}

/** The selectable parent of ``node`` — its parent, unless that parent is the
 *  synthetic root (a per-model file root has no selectable level above it). */
export function selectableParent(node: TreeNodeData, idx: TreeIndices): TreeNodeData | null {
    const par = idx.parent.get(node.id) ?? null;
    if (!par || par === idx.root) return null;
    return par;
}
