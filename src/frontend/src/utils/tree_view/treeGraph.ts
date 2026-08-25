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

/** What the info panel can say about a selection-tree node from the client alone. */
export interface NodeFacts {
    /** The node's unique tree id. */
    id: string;
    /** Display name. Not unique — see ``id``. */
    name: string;
    /**
     * Whether the node itself carries geometry. False for a spatial container:
     * containers hold no draw range of their own, only descendants that do.
     */
    hasGeometry: boolean;
    /** Direct children. */
    childCount: number;
    /** Geometry-bearing nodes at or below this one. */
    descendantGeometryCount: number;
    /** Ancestors, nearest first, excluding the node itself and the synthetic root. */
    ancestry: TreeNodeData[];
}

/**
 * Describe a node using only what the client already holds.
 *
 * This exists because selecting a **spatial container** used to leave the
 * selected-object panel with nothing to render: that panel is driven by
 * geometry metadata keyed on a physical object's display name, and a container
 * has neither a draw range nor an entry in that map. Yet the interesting facts
 * about a container — where it sits, how much it holds, whether it is a leaf
 * that simply failed to resolve or a group that legitimately has no geometry of
 * its own — are all already in the tree the client built.
 *
 * Deliberately generic: nodes carry no type in the hierarchy contract (which is
 * ``(name, parent)`` per node and nothing else), so this reports *structure*,
 * never a classification it would have to invent. Producers that do know a
 * node's kind can attach it through the separate stable-key channel.
 */
export function describeNode(node: TreeNodeData, idx: TreeIndices): NodeFacts {
    // Iterative, not recursive: a deep tree must not be able to blow the stack,
    // and the visited guard keeps a malformed parent link from looping forever.
    let descendantGeometryCount = 0;
    const seen = new Set<string>();
    const stack: TreeNodeData[] = [node];
    while (stack.length) {
        const cur = stack.pop()!;
        if (seen.has(cur.id)) continue;
        seen.add(cur.id);
        if (cur.node_name) descendantGeometryCount++;
        for (const child of cur.children ?? []) stack.push(child);
    }
    // ancestorChain is leaf-first and starts with the node itself; drop that,
    // and drop the synthetic root, which is not a place in the model.
    const ancestry = ancestorChain(node, idx)
        .slice(1)
        .filter((n) => n !== idx.root);
    return {
        id: node.id,
        name: node.name,
        hasGeometry: !!node.node_name,
        childCount: node.children?.length ?? 0,
        descendantGeometryCount,
        ancestry,
    };
}
