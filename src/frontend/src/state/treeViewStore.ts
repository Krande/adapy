import {create} from 'zustand';
import {TreeApi} from "react-arborist";
import {TreeNodeData} from "../components/tree_view/CustomNode";
import {labelRoots, readRootLabelMode, writeRootLabelMode, type RootLabelMode} from "@/utils/tree_view/rootLabels";

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

/** `buildRangeIndex` per model, for the container of loaded models: `model_key -> (key -> node)`.
 *
 * ONE MODEL'S INDEX IS BUILT ONCE. Rebuilding the whole index on every `setTreeData` walked every
 * node of every model already loaded -- a bulk load of 60 models re-indexed the first one 60 times,
 * and the load profile showed each load's preparation growing with the scene. A root's index is
 * cached on its `children` array, which a relabel (`labelRoots` copies the root row, not what is
 * under it) and a sibling's arrival both leave alone, so only a newly loaded model is walked. */
const rootIndexes = new WeakMap<TreeNodeData[], Map<string, Map<string, TreeNodeData>>>();

export function indexByModel(container: TreeNodeData): Map<string, Map<string, TreeNodeData>> {
    const out = new Map<string, Map<string, TreeNodeData>>();
    // Maps in `out` that are this call's own copies, not a root's cached one: only those may be
    // merged into (two roots under one model key -- a reload -- must not edit each other's cache).
    const owned = new Set<Map<string, TreeNodeData>>();
    const isContainer = container.id === "__roots__";
    for (const root of isContainer ? container.children : [container]) {
        let perModel = isContainer ? rootIndexes.get(root.children) : undefined;
        if (!perModel) {
            perModel = new Map();
            for (const [key, node] of buildRangeIndex(root)) {
                const model = node.model_key ?? "";
                let m = perModel.get(model);
                if (!m) perModel.set(model, (m = new Map()));
                m.set(key, node);
            }
            if (isContainer) rootIndexes.set(root.children, perModel);
        }
        for (const [model, m] of perModel) {
            let have = out.get(model);
            if (!have) {
                out.set(model, m);
                continue;
            }
            if (!owned.has(have)) {
                have = new Map(have);
                owned.add(have);
                out.set(model, have);
            }
            for (const [k, v] of m) have.set(k, v);
        }
    }
    return out;
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

    /** Whether a model's root row shows its top-level name or the unique id it was loaded under. */
    rootLabelMode: RootLabelMode;
    setRootLabelMode: (mode: RootLabelMode) => void;
}

let rangeIndex: Map<string, Map<string, TreeNodeData>> = new Map();

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
        rangeIndex = indexByModel(data);
        set({treeData: data});
    },
    clearTreeData: () => {
        rangeIndex = new Map();
        set({treeData: null});
    },
    findNodeByRangeId: (modelKey, rangeId) => rangeIndex.get(modelKey)?.get(`${modelKey}|${rangeId}`) ?? null,
    isTreeCollapsed: true,
    setIsTreeCollapsed: (collapsed) => set({isTreeCollapsed: collapsed}),
    treeViewWidth: 256,
    setTreeViewWidth: (w) => set({treeViewWidth: w}),
    setMaxId: (max_id) => set({max_id: max_id}),
    rootLabelMode: readRootLabelMode(),
    setRootLabelMode: (mode) => {
        writeRootLabelMode(mode);
        set((s) => {
            if (!s.treeData) return {rootLabelMode: mode};
            const treeData = labelRoots(s.treeData, mode);
            rangeIndex = indexByModel(treeData);
            return {rootLabelMode: mode, treeData};
        });
    },
}));