// state/cacheModelUtils.ts
import {modelStore} from "./modelStore";
import {useTreeViewStore} from "../treeViewStore";
import {TreeNodeData} from "../../components/tree_view/CustomNode";

/**
 * 1) cache hierarchy + drawRanges
 * 2) build the parent/child tree in the worker
 * 3) set treeData in your Zustand store
 */
export async function cacheAndBuildTree(
    key: string,
    rawUserData: Record<string, any>
): Promise<void> {
    const tree_store = useTreeViewStore.getState()

    // 1) extract
    const hierarchy = (rawUserData["id_hierarchy"] ?? {}) as Record<
        string,
        [string, string | number]
    >;

    const drawRanges: Record<string, Record<number, [number, number]>> = {};
    for (const k of Object.keys(rawUserData).filter((k) =>
        k.startsWith("draw_ranges_node")
    )) {
        const idx = k.slice("draw_ranges_node".length);
        drawRanges[`node${idx}`] = rawUserData[k] as Record<
            number,
            [number, number]
        >;
    }

    // 2) cache → IndexedDB
    try {
        await modelStore.add(key, hierarchy, drawRanges);
    } catch (err: unknown) {
        console.error("Failed to cache model metadata", err);
        // you could even early-return here if caching is critical
    }

    // 3) build hierarchy off the main thread
    let treeData: TreeNodeData | null;
    try {
        treeData = await modelStore.buildHierarchy(key, hierarchy, tree_store.max_id + 1);
    } catch (err: unknown) {
        console.error("Failed to build tree hierarchy", err);
        return;
    }

    // 4) populate your store
    if (treeData) {
        if (tree_store.treeData && tree_store.treeData.children.length > 0) {
            // if treeData is not empty, make a new root node "root" if not already exist and place the treeData under it
            let existing_root = tree_store.treeData?.children.find((child) => child.name === "root");
            if (!existing_root) {
                // create a new root node with unique uuid key
                existing_root = {
                    id: (tree_store.max_id + 1).toString(),
                    name: "root",
                    children: [treeData, tree_store.treeData],
                    model_key: key,
                    node_name: null,
                };
            } else {
                existing_root.children = [...existing_root.children, treeData];
            }
            tree_store.setTreeData(existing_root);
        } else {
            tree_store.setTreeData(treeData);

        }
        // find the highest id number from all recursive children and set the state
        const max_id = await get_max_child_id(treeData);
        tree_store.setMaxId(max_id + 1);
    }
}

// Recursive function to get the maximum child id
async function get_max_child_id(
    node: TreeNodeData,
    max_id: number = 0
): Promise<number> {
    if (node.children.length === 0) {
        return Math.max(Number(node.id), max_id);
    }
    for (const child of node.children) {
        max_id = await get_max_child_id(child, max_id);
    }
    return max_id;
}