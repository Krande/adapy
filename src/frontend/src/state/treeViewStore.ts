import {create} from 'zustand';
import {TreeApi} from "react-arborist";

export interface TreeNode {
    id: string;
    name: string;
    children: TreeNode[];
}

interface TreeViewState {
    treeData: TreeNode | null;
    tree: TreeApi<any> | null;
    selectedNodeId: string | null;
    setTreeData: (data: TreeNode) => void;
    clearTreeData: () => void;
    setSelectedNodeId: (id: string | null) => void;
    isCollapsed: boolean;
    setIsCollapsed: (collapsed: boolean) => void;
    setTree: (tree: TreeApi<any>) => void;
}

export const useTreeViewStore = create<TreeViewState>((set) => ({
    treeData: null,
    tree: null,
    setTree: (tree) => set({tree: tree}),
    selectedNodeId: null,
    setTreeData: (data) => set({treeData: data}),
    clearTreeData: () => set({treeData: null}),
    setSelectedNodeId: (id) => set({selectedNodeId: id}),
    isCollapsed: true,
    setIsCollapsed: (collapsed) => set({isCollapsed: collapsed}),
}));