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
    setTreeData: (data: TreeNode) => void;
    clearTreeData: () => void;
    isCollapsed: boolean;
    setIsCollapsed: (collapsed: boolean) => void;
    setTree: (tree: TreeApi<any>) => void;
    searchTerm: string;
    setSearchTerm: (searchTerm: string) => void;
}

export const useTreeViewStore = create<TreeViewState>((set) => ({
    treeData: null,
    tree: null,
    searchTerm: '',
    setSearchTerm: (searchTerm) => set({searchTerm: searchTerm}),
    setTree: (tree) => set({tree: tree}),
    setTreeData: (data) => set({treeData: data}),
    clearTreeData: () => set({treeData: null}),
    isCollapsed: true,
    setIsCollapsed: (collapsed) => set({isCollapsed: collapsed}),
}));