import {create} from 'zustand';

export interface TreeNode {
    id: string;
    name: string;
    children: TreeNode[];
}

interface TreeViewState {
    treeData: TreeNode | null;
    selectedNodeId: string | null;
    setTreeData: (data: TreeNode) => void;
    clearTreeData: () => void;
    setSelectedNodeId: (id: string | null) => void;
    isCollapsed: boolean;
    setIsCollapsed: (collapsed: boolean) => void;
}

export const useTreeViewStore = create<TreeViewState>((set) => ({
    treeData: null,
    selectedNodeId: null,
    setTreeData: (data) => set({treeData: data}),
    clearTreeData: () => set({treeData: null}),
    setSelectedNodeId: (id) => set({selectedNodeId: id}),
    isCollapsed: true,
    setIsCollapsed: (collapsed) => set({isCollapsed: collapsed}),
}));