import {create} from 'zustand';
import {TreeApi} from "react-arborist";
import {TreeNodeData} from "../components/tree_view/CustomNode";

export interface TreeNode {
    id: string;
    name: string;
    children: TreeNode[];
}

export interface TreeViewState {
    treeData: TreeNodeData | null;
    tree: TreeApi<any> | null;
    setTreeData: (data: TreeNodeData) => void;
    clearTreeData: () => void;
    isTreeCollapsed: boolean;
    setIsTreeCollapsed: (collapsed: boolean) => void;
    setTree: (tree: TreeApi<any>) => void;
    searchTerm: string;
    setSearchTerm: (searchTerm: string) => void;
    max_id: number

    setMaxId(max_id: number): void;
}

export const useTreeViewStore = create<TreeViewState>((set) => ({
    treeData: null,
    tree: null,
    searchTerm: '',
    max_id: 0,
    setSearchTerm: (searchTerm) => set({searchTerm: searchTerm}),
    setTree: (tree) => set({tree: tree}),
    setTreeData: (data) => set({treeData: data}),
    clearTreeData: () => set({treeData: null}),
    isTreeCollapsed: true,
    setIsTreeCollapsed: (collapsed) => set({isTreeCollapsed: collapsed}),
    setMaxId: (max_id) => set({max_id: max_id}),
}));