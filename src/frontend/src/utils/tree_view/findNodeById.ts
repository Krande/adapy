import {TreeNode} from "../../state/treeViewStore";

export function findNodeById(node: TreeNode, id: string): TreeNode | null {
    if (node.name === id) {
        return node;
    }
    if (!Array.isArray(node.children)) {
        return null;
    }
    for (let child of node.children) {
        const result = findNodeById(child, id);
        if (result) {
            return result;
        }
    }
    return null
}