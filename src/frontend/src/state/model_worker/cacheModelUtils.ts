// state/cacheModelUtils.ts
import {modelStore, PureTreeNode} from "./modelStore";
import {useTreeViewStore} from "../treeViewStore";

/**
 * 1) cache hierarchy + drawRanges
 * 2) build the parent/child tree in the worker
 * 3) set treeData in your Zustand store
 */
export async function cacheAndBuildTree(
    key: string,
    rawUserData: Record<string, any>
): Promise<void> {
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
    let pureTree: PureTreeNode | null;
    try {
        pureTree = await modelStore.buildHierarchy(key, hierarchy);
    } catch (err: unknown) {
        console.error("Failed to build tree hierarchy", err);
        return;
    }

    // 4) populate your store
    if (pureTree) {
        useTreeViewStore.getState().setTreeData(pureTree);
    }
}
