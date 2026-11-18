// Whether the joints check (clashes) can run on a row, and the action that starts it.

import type { AssetView } from "@/assets/assetView";
import { rowFacts, type RowBadge } from "@/assets/rowFacts";
import { useClashCheckStore } from "@/state/clashCheckStore";
import { useSceneInfoStore } from "@/state/sceneInfoStore";
import { CORE_PANEL_IDS, reveal } from "@/layout/panelActions";

/** Check a published node for joints, and take the answer where joints are already shown.
 *
 *  A context-menu action, not a button in the detail: it is an occasional analysis, and the
 *  detail's bottom row is kept for what a row is FOR -- putting it in the scene.
 *
 *  WHY THE RESULT IS NOT HERE. The Clashes panel already renders a result -- groups, types, the
 *  detail hand-off, the producer filter -- and a second joints table in this tab would be a second
 *  implementation of the same reading, free to disagree with it. So this switches to that panel
 *  and runs the check there; the panel shows it running, and shows a failure, itself. That is also
 *  where a user who ran one from a FILE ends up. The two ways in converge on one surface.
 *
 *  OFFERED ONLY FOR A NODE SOMETHING IS PUBLISHED AT OR ABOVE. A check reads the published
 *  source, so a row with no covering publish has nothing to read -- and an item that enqueued a
 *  job which 404s is worse than no item.
 *
 *  THE SUBJECT IS THE COVERING PUBLISH, not always this row: a leaf published under a root has no
 *  manifest of its own, and the badge already resolved which one speaks for it.
 */
export function useJointsCheck(view: AssetView, id: string, scope: string) {
    const busy = useClashCheckStore((s) => s.busy);
    const setAssetTarget = useClashCheckStore((s) => s.setAssetTarget);
    const runCheck = useClashCheckStore((s) => s.runCheck);
    const setMode = useSceneInfoStore((s) => s.setMode);

    const badge: RowBadge | null = rowFacts(view, id)?.badge ?? null;
    if (!badge) return null;
    const run = () => {
        setAssetTarget(`${view.collection} / ${id}`, {
            kind: "node",
            collection: view.collection,
            subject: badge.at,
            revision: badge.revision,
            node: id,
            provider: badge.provider,
            collectionProviders: view.contentProviders,
        });
        setMode("clashes");
        reveal(CORE_PANEL_IDS.scene);
        void runCheck(scope);
    };
    return { busy, run };
}
