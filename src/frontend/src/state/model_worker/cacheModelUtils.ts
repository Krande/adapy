// state/cacheModelUtils.ts
import {modelStore} from "./modelStore";
import {useTreeViewStore} from "../treeViewStore";
import {useOptionsStore} from "@/state/optionsStore";
import {clearFaceHighlight} from "@/utils/mesh_select/faceHighlight";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {TreeNodeData} from "@/components/tree_view/CustomNode";
import {labelRoots, topLevelName} from "@/utils/tree_view/rootLabels";

/**
 * 1) cache hierarchy + drawRanges
 * 2) build the parent/child tree in the worker
 * 3) set treeData in your Zustand store
 */
// Synthetic container holding one root per loaded model. Its children are the
// per-model roots (labelled by GLB filename); it is never rendered itself —
// TreeViewComponent renders ``treeData.children``.
const ROOTS_CONTAINER_ID = "__roots__";

function filenameLabel(sourceName: string | undefined, fallback: string): string {
    if (!sourceName) return fallback || "model";
    const base = sourceName.split("/").pop() || sourceName;
    return base || fallback || "model";
}


export async function cacheAndBuildTree(
    key: string,
    rawUserData: Record<string, any>,
    sourceName?: string,
    /** What the caller calls this model -- a site's label, a model's name -- for its root row. */
    displayName?: string,
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

    // Opt-in per-face clickable regions: face_ranges_node<idx> = {rangeId: [[start,len,faceId,seq],...]}
    // where start/len are relative to that solid's draw range. Present only for GLBs converted with
    // face-region capture; absent otherwise (the faces toggle stays hidden).
    const faceRanges: Record<string, Record<number, [number, number, number, number][]>> = {};
    for (const k of Object.keys(rawUserData).filter((k) =>
        k.startsWith("face_ranges_node")
    )) {
        const idx = k.slice("face_ranges_node".length);
        faceRanges[`node${idx}`] = rawUserData[k] as Record<
            number,
            [number, number, number, number][]
        >;
    }

    // Advertise face-region availability so the scene-info solid/faces toggle appears (or hides).
    const hasFaceRegions = Object.keys(faceRanges).length > 0;
    const opts = useOptionsStore.getState();
    opts.setFaceRegionsAvailable(hasFaceRegions);
    // Loading a model that can't do face picking snaps the mode back to Solid, so the toggle (now
    // hidden) can't leave clicks stuck on the raycast path with no face id ever resolving.
    if (!hasFaceRegions && opts.faceLevelPicking) {
        opts.setFaceLevelPicking(false);
        clearFaceHighlight();
        useObjectInfoStore.getState().setClickedFace(null);
    }

    // 2) cache → IndexedDB
    try {
        await modelStore.add(key, hierarchy, drawRanges, faceRanges);
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

    // 4) populate the store: one root per loaded model, labelled by GLB
    //    filename (deduped with -2/-3 on collision), held under a synthetic
    //    container whose children TreeViewComponent renders as the top level.
    if (treeData) {
        treeData.model_key = key;
        treeData.source_name = sourceName ?? null;

        const prev = tree_store.treeData;
        const isContainer = !!prev && prev.id === ROOTS_CONTAINER_ID;
        // Existing roots, minus any prior load of this same model (reload replaces).
        const siblings = isContainer
            ? prev!.children.filter((c) => c.model_key !== key)
            : prev
                ? [prev].filter((c) => c.model_key !== key)
                : [];

        // Both names on the root; which one shows is the viewer's choice (`rootLabels`).
        treeData.source_label = filenameLabel(sourceName, treeData.name);
        treeData.top_name = topLevelName(displayName, treeData.name, treeData.source_label);

        const container: TreeNodeData = {
            id: ROOTS_CONTAINER_ID,
            name: "",
            children: [...siblings, treeData],
            model_key: null,
            node_name: null,
        };
        tree_store.setTreeData(labelRoots(container, tree_store.rootLabelMode));

        // Only the NEW model's ids can raise the maximum -- they were numbered from it -- so only
        // its subtree is walked. Walking the whole container (every model loaded so far, an await
        // per node) made each load of a bulk load slower than the one before.
        tree_store.setMaxId(Math.max(useTreeViewStore.getState().max_id, maxNodeId(treeData)) + 1);
    }
}

/** The largest id anywhere under `root`, iteratively: a deep tree must not cost a stack frame (or a
 *  promise) per node. EVERY node, not only leaves -- ids are handed out in `id_hierarchy` order,
 *  so an assembly row can hold the largest one, and the next model numbered from a leaf maximum
 *  reused it: two rows with one id, and a pick selecting the wrong one. */
export function maxNodeId(root: TreeNodeData): number {
    let max = 0;
    const stack: TreeNodeData[] = [root];
    for (let node = stack.pop(); node; node = stack.pop()) {
        const n = Number(node.id);
        if (Number.isFinite(n)) max = Math.max(max, n);
        for (const child of node.children) stack.push(child);
    }
    return max;
}