import {create} from 'zustand';
import {TreeApi} from "react-arborist";
import {TreeNodeData} from "../components/tree_view/CustomNode";

export interface TreeNode {
    id: string;
    name: string;
    children: TreeNode[];
}

/** Reverse index ``"<model_key>|<rangeId>" -> tree node`` over a (container) tree.
 *
 * Selection sync must resolve a picked (mesh, rangeId) to its tree row by the
 * model's UNIQUE numeric node id — display names repeat thousands of times in
 * real CAD models, and rangeIds restart at 0 in every loaded GLB, so only the
 * composite key is unique. Values are references to the live TreeNodeData
 * objects (no copies); ~24k entries cost a couple of MB and one O(1) lookup
 * replaces an O(n) recursive name scan plus a worker round-trip per range. */
export function buildRangeIndex(root: TreeNodeData): Map<string, TreeNodeData> {
    const index = new Map<string, TreeNodeData>();
    const stack: TreeNodeData[] = [root];
    while (stack.length) {
        const n = stack.pop()!;
        if (n.rangeId != null && n.model_key != null) {
            index.set(`${n.model_key}|${n.rangeId}`, n);
        }
        if (Array.isArray(n.children)) for (const c of n.children) stack.push(c);
    }
    return index;
}

export interface TreeViewState {
    treeData: TreeNodeData | null;
    tree: TreeApi<any> | null;
    setTreeData: (data: TreeNodeData) => void;
    clearTreeData: () => void;
    /** O(1) selection-sync lookup by the only globally-unique identity a picked
     *  range has: the loaded model's key plus the GLB's numeric node id. */
    findNodeByRangeId: (modelKey: string, rangeId: string) => TreeNodeData | null;
    isTreeCollapsed: boolean;
    setIsTreeCollapsed: (collapsed: boolean) => void;
    setTree: (tree: TreeApi<any>) => void;
    searchTerm: string;
    setSearchTerm: (searchTerm: string) => void;
    /** Id of the currently-selected tree node. Search is scoped to this node's
     *  subtree; when null, search spans all roots (hits group per root). */
    scopeNodeId: string | null;
    scopeNodeName: string | null;
    setScope: (id: string | null, name: string | null) => void;
    max_id: number
    /** Width of the floating tree panel in pixels. Lifted out of
     *  ResizableTreeView's local state so the menu bar can shift to
     *  the right of it on desktop without overlapping. */
    treeViewWidth: number;
    setTreeViewWidth: (w: number) => void;

    setMaxId(max_id: number): void;
}

let rangeIndex: Map<string, TreeNodeData> = new Map();

export const useTreeViewStore = create<TreeViewState>((set) => ({
    treeData: null,
    tree: null,
    searchTerm: '',
    scopeNodeId: null,
    scopeNodeName: null,
    setScope: (id, name) => set({scopeNodeId: id, scopeNodeName: name}),
    max_id: 0,
    setSearchTerm: (searchTerm) => set({searchTerm: searchTerm}),
    setTree: (tree) => set({tree: tree}),
    setTreeData: (data) => {
        rangeIndex = buildRangeIndex(data);
        set({treeData: data});
    },
    clearTreeData: () => {
        rangeIndex = new Map();
        set({treeData: null});
    },
    findNodeByRangeId: (modelKey, rangeId) => rangeIndex.get(`${modelKey}|${rangeId}`) ?? null,
    isTreeCollapsed: true,
    setIsTreeCollapsed: (collapsed) => set({isTreeCollapsed: collapsed}),
    treeViewWidth: 256,
    setTreeViewWidth: (w) => set({treeViewWidth: w}),
    setMaxId: (max_id) => set({max_id: max_id}),
}));