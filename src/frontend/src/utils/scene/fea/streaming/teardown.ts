// FEA streaming: teardown.
//
// Owns: ending the FEA model session and resetting every store that was
// showing it. One function, called unconditionally by every scene-clearing
// path, so nothing here may assume a session is open.
//
// Inputs: the session store (closed here), `useFeaAnimationStore`,
// `useColorStore`, `useTableNavStore`, `useAnimationStore`, the animation
// driver and the go-to-node marker.

import {useAnimationStore} from "@/state/animationStore";
import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useModelSessionStore} from "@/state/modelSession";
import {useTableNavStore} from "@/state/tableNavStore";
import {resetFeaAnimationPhase} from "../feaAnimationDriver";
import {clearGoToNode} from "../goToNode";

/** Drop the cached state on next call (e.g. when the user replaces
 * the scene with a different file). The blob cache lives separately
 * in feaFieldBlob.ts. Also resets the deformation-animation store
 * so the SimulationControls UI doesn't keep showing FEA-mode
 * controls for a mesh that's no longer in the scene, and hides the
 * controls panel entirely when no GLTF clips are around to show in
 * the fallback path. */
export function clearActiveFeaStreaming(): void {
    // Ends the model session. The FEA handle, the per-source group refs and
    // the identity all go together, and so do the colour-owner stack's saved
    // views — the next load is a new source even if it is the same file again
    // (what `noteFieldSourceCleared` used to say here, now `close()`'s job).
    useModelSessionStore.getState().close();
    useFeaAnimationStore.getState().reset();
    useColorStore.getState().setShowLegend(false);
    resetFeaAnimationPhase();
    // Drop any "go to node" marker + active-row state. The marker
    // mesh would otherwise survive into the next loaded model and
    // point at a vertex that no longer exists.
    clearGoToNode();
    useTableNavStore.getState().setActiveNodeId(null);
    useTableNavStore.getState().setGoToTarget(null);
    // Hide the panel — without this, the toggle button stays
    // pressed-state on a panel that has nothing useful to show.
    // Re-applying an FEA session sets it back to true.
    const generalAnimStore = useAnimationStore.getState();
    if (!generalAnimStore.hasAnimation) {
        generalAnimStore.setIsControlsVisible(false);
    }
}
