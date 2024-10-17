import * as THREE from 'three';
import {TreeNode} from "../../state/treeViewStore";

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