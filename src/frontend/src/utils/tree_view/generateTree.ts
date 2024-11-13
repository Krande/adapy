import * as THREE from 'three';
import {TreeNode} from "../../state/treeViewStore";

// Utility function to generate tree data from the scene, excluding LineSegments
const generateTree = (object: THREE.Object3D): TreeNode | null => {
    // Check if the object is a LineSegments and skip it
    if (object instanceof THREE.LineSegments) {
        return null;
    }
    let name = object.name  || object.type;
    if (name == "Group") {
        name = "Scene";
    }

    return {
        id: object.uuid,
        name: name,
        // Filter out null children (those that are LineSegments)
        children: object.children
            .map((child) => generateTree(child))
            .filter((child): child is TreeNode => child !== null),
    };
};
export const buildTreeFromScene = (scene: THREE.Scene): TreeNode | null => {
    return generateTree(scene);
}

export const buildTreeFromUserData = (scene: THREE.Scene): TreeNode | null => {
    let hierarchy: Record<string, [string, string | number]> = scene.userData["id_hierarchy"];
    if (!hierarchy) return null;

    // Step 1: Create a map to hold all TreeNodes by id
    let nodes: Record<string, TreeNode> = {};

    // Initialize TreeNode objects for each entry in the hierarchy
    for (let [id, [name]] of Object.entries(hierarchy)) {
        nodes[id] = {
            id,
            name,
            children: []
        };
    }

    // Step 2: Build the tree structure by linking parent-child relationships
    let root: TreeNode | null = null;
    for (let [id, [, parentId]] of Object.entries(hierarchy)) {
        if (parentId === "*" || parentId === null) {
            // Root node
            root = nodes[id];
        } else {
            // Add to parent's children array
            let parentNode = nodes[parentId];
            if (parentNode) {
                parentNode.children.push(nodes[id]);
            }
        }
    }

    // Step 3: Sort children by name using natural sorting
    const sortChildren = (node: TreeNode) => {
        node.children.sort((a, b) => {
            return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
        });
        node.children.forEach(sortChildren); // Recursively sort all children
    };

    // Start sorting from the root
    if (root) sortChildren(root);

    return root;
}