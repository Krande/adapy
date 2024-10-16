import {create} from 'zustand';
import * as THREE from 'three';

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

// Utility function to generate tree data from the scene, excluding LineSegments
export const generateTree = (object: THREE.Object3D): TreeNode | null => {
    // Check if the object is a LineSegments and skip it
    if (object instanceof THREE.LineSegments) {
        return null;
    }

    return {
        id: object.uuid,
        name: object.name || object.type,
        // Filter out null children (those that are LineSegments)
        children: object.children
            .map((child) => generateTree(child))
            .filter((child): child is TreeNode => child !== null),
    };
};