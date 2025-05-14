// state/cacheModelUtils.ts
import { modelStore, PureTreeNode } from "./modelStore";
import { useTreeViewStore } from "../treeViewStore";
import {TreeNodeData} from "../../components/tree_view/CustomNode";

/**
 * 1) caches hierarchy + drawRanges
 * 2) offloads the pure tree build to the worker
 * 3) sets treeData in your Zustand store when ready
 */
export async function cacheAndBuildTree(
  key: string,
  rawUserData: Record<string, any>
): Promise<void> {
  // Extract the raw JSON pieces
  const hierarchy = (rawUserData["id_hierarchy"] ?? {}) as Record<
    string,
    [string, string | number]
  >;

  const drawRanges: Record<string, Record<number, [number, number]>> = {};
  Object.keys(rawUserData)
    .filter((k) => k.startsWith("draw_ranges_node"))
    .forEach((k) => {
      const idx = k.slice("draw_ranges_node".length);
      drawRanges[`node${idx}`] = rawUserData[k] as Record<
        number,
        [number, number]
      >;
    });

  // 1) cache into IndexedDB via Dexie+Comlink
  modelStore
    .add(key, hierarchy, drawRanges)
    .catch((err: unknown): void =>
      console.error("Failed to cache model metadata", err)
    );

  // 2) build the parent/child tree off the main thread
  modelStore
    .buildHierarchy(key, hierarchy)
    .then((pureTree: TreeNodeData | null): void => {
      if (pureTree) {
        // 3) update your UI-side store
        useTreeViewStore.getState().setTreeData(pureTree);
      }
    })
    .catch((err: unknown): void =>
      console.error("Failed to build tree hierarchy", err)
    );
}
