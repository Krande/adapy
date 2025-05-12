import * as THREE from 'three';
import {TreeNode} from "../../state/treeViewStore";
import {getDrawRangeByName} from "../mesh_select/getDrawRangeByName";
import {getMeshFromName} from "../scene/getMeshFromName";
import {TreeNodeData} from "../../components/tree_view/CustomNode";

// Utility function to generate tree data from the scene, excluding LineSegments
const generateTree = (object: THREE.Object3D): TreeNode | null => {
    // Check if the object is a LineSegments and skip it
    if (object instanceof THREE.LineSegments) {
        return null;
    }
    let name = object.name || object.type;
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


export const buildTreeFromUserData = (userdata: any): TreeNode | null => {
    let hierarchy: Record<string, [string, string | number]> = userdata["id_hierarchy"];
    if (!hierarchy) return null;

    // Step 1: Create a map to hold all TreeNodes by id
    let nodes: Record<string, TreeNodeData> = {};

    // Initialize TreeNode objects for each entry in the hierarchy
    for (let [id, [name]] of Object.entries(hierarchy)) {
        let draw_range_data = getDrawRangeByName(name);
        let rangeId = null;
        let key = null;
        let mesh = null;
        if (draw_range_data) {
            [key, rangeId] = draw_range_data;
            let mesh_node_name = key.split("_")[2];
            //console.time("getMeshFromName");
            mesh = getMeshFromName(mesh_node_name);
        }

        nodes[id] = {
            id,
            name,
            children: [],
            key: key,
            rangeId: rangeId,
            meshRef: mesh,
        };
    }

    // Step 2: Build the tree structure by linking parent-child relationships
    let root: TreeNodeData | null = null;
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
            return a.name.localeCompare(b.name, undefined, {numeric: true, sensitivity: 'base'});
        });
        node.children.forEach(sortChildren); // Recursively sort all children
    };

    // Start sorting from the root
    if (root) sortChildren(root);

    return root;
}