// state/cacheModelUtils.ts
import {modelStore} from "./modelStore";
import {useOptionsStore} from "@/state/optionsStore";
import {clearFaceHighlight} from "@/utils/mesh_select/faceHighlight";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {addModelTree} from "./addModelTree";

export {maxNodeId} from "./addModelTree";

/**
 * 1) cache hierarchy + drawRanges
 * 2) build the parent/child tree in the worker
 * 3) set treeData in your Zustand store -- one model's tree after another (`./addModelTree`)
 */
export async function cacheAndBuildTree(
    key: string,
    rawUserData: Record<string, any>,
    sourceName?: string,
    /** What the caller calls this model -- a site's label, a model's name -- for its root row. */
    displayName?: string,
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

    // 3) build hierarchy off the main thread, 4) populate the store: after every model whose tree
    //    was asked for before this one, so neither drops the other's root.
    await addModelTree(key, (startId) => modelStore.buildHierarchy(key, hierarchy, startId), sourceName, displayName);
}
