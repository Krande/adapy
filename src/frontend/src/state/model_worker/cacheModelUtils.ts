// state/cacheModelUtils.ts
import {modelStore} from "./modelStore";
import {useTreeViewStore} from "../treeViewStore";
import {useOptionsStore} from "@/state/optionsStore";
import {TreeNodeData} from "@/components/tree_view/CustomNode";

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

// Append -2 / -3 / ... when an identical filename is already a root.
function dedupLabel(base: string, existing: string[]): string {
    if (!existing.includes(base)) return base;
    let n = 2;
    while (existing.includes(`${base}-${n}`)) n++;
    return `${base}-${n}`;
}

export async function cacheAndBuildTree(
    key: string,
    rawUserData: Record<string, any>,
    sourceName?: string,
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
    useOptionsStore.getState().setFaceRegionsAvailable(Object.keys(faceRanges).length > 0);

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

        const prev = tree_store.treeData;
        const isContainer = !!prev && prev.id === ROOTS_CONTAINER_ID;
        // Existing roots, minus any prior load of this same model (reload replaces).
        const siblings = isContainer
            ? prev!.children.filter((c) => c.model_key !== key)
            : prev
                ? [prev].filter((c) => c.model_key !== key)
                : [];

        treeData.name = dedupLabel(
            filenameLabel(sourceName, treeData.name),
            siblings.map((c) => c.name),
        );

        const container: TreeNodeData = {
            id: ROOTS_CONTAINER_ID,
            name: "",
            children: [...siblings, treeData],
            model_key: null,
            node_name: null,
        };
        tree_store.setTreeData(container);

        const max_id = await get_max_child_id(container);
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