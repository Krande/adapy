// The solver node ids of the loaded result, aligned to the mesh's points, for
// the number labels. A bake without them numbers nodes by point index + 1 (what
// "go to node" does), which the caller falls back to while this is null.

import {capabilities} from "@/services/capabilities";
import {manifestNodeLabels} from "@/services/fea/feaFieldDetail";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";

import {feaSourceScope} from "./streaming/session";

/** Resolve the node ids of the active result; null when it has none or the fetch fails. */
export async function loadNodeLabelTable(): Promise<number[] | null> {
    const {sourceName, manifest} = useFeaAnimationStore.getState();
    if (!sourceName || !manifest) return null;
    try {
        const {fetcher, cacheKey} = capabilities.fea.fieldFetchers(feaSourceScope(sourceName), sourceName);
        return await manifestNodeLabels(manifest, fetcher, cacheKey);
    } catch {
        return null;
    }
}
