// THE way to empty the scene, for every trigger: the storage browser's Clear, the Scene tab's Remove
// all (its tree menu, the command palette, a plugin's `ctx.scene.clearScene`), a scope switch, the
// gallery's next stop, a re-convert of the loaded model, unloading a streamed FEA result, and every
// load that REPLACES the scene (`replace_model`, the three.js-free viewport's replace, a renderer
// switch handing the models over). Each of them ends here, so whatever is added to the teardown
// below applies to all of them; a trigger that keeps something says so with an option rather than
// with a teardown of its own. A guard test holds that nothing else calls the primitives this calls.
//
// What goes:
//   * the models, through the viewport on screen whichever renderer it is (three.js or the
//     three.js-free one), and the three.js scene's groups in any case: they may be left over from
//     before a renderer switch, and would come back with the three.js viewport;
//   * the bookkeeping that names them: the loaded sources, the tree, the selection, the lineage, the
//     streamed groups, the GLTF simulation data, the animation state, the FEA streaming session;
//   * what is drawn over them: measurements (the Measure tab), the connection builder's annotations,
//     weld colours and marks of the joint it previewed and that preview's clash, every highlight;
//   * unless kept: loads still queued (they would arrive into the scene just cleared) and an open
//     procedural model (its proxies are part of what is in the scene).
//
// What stays: a plugin's own line, point and image layers (`ctx.scene.addLineLayer` and the like).
// They are not a model's; the plugin that drew them takes them away.

import {getSingletonViewerStores} from "@/state/AdaViewerContext";
import {useAnimationStore} from "@/state/animationStore";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {clearMeasurements} from "@/state/measureStore";
import {useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {endAllSceneHighlights} from "@/plugins/sceneHighlights";
import {forgetPreviewIfUnloaded} from "@/services/devConnections/previewOnShow";
import {clearThreeSceneModels} from "@/utils/scene/clearThreeSceneModels";
import {clearConnectionOverlay} from "@/utils/scene/connectionOverlay";
import {clearWeldMarks} from "@/utils/scene/connectionWeldMarks";
import {clearWeldTint} from "@/utils/scene/connectionWeldTint";
import {activeViewport} from "@/viewport/api/viewportApi";
import {clearActiveFeaStreaming} from "./load_fea_streaming";

export interface ClearSceneOptions {
    /** Who asked (a scope change, a replace, ...): for whoever reads a call of it; not acted on. */
    reason?: string;
    /** A load is about to put a model in place of what is cleared (`replace_model`, the
     *  three.js-free viewport's replace). What that load sets itself is left to it: the model URL
     *  that started it, the groups a streamed result announces before its mesh arrives, the GLTF
     *  simulation data. */
    replacing?: boolean;
    /** Keep the loads still queued: they are what comes next (a replace from the queue, the gallery,
     *  a re-convert, unloading one streamed result). Dropped otherwise. */
    keepQueuedLoads?: boolean;
    /** Keep an open procedural model. Closed otherwise. */
    keepProcedural?: boolean;
}

export async function clearScene(options: ClearSceneOptions = {}): Promise<void> {
    const {replacing = false, keepQueuedLoads = false, keepProcedural = false} = options;

    if (!keepQueuedLoads) {
        const {useLoadQueueStore} = await import("@/state/loadQueueStore");
        const queue = useLoadQueueStore.getState();
        for (const t of queue.queued) queue.removeQueued(t.name);
    }

    const animationStore = useAnimationStore.getState();
    animationStore.setHasAnimation(false);
    animationStore.setIsPlaying(false);
    animationStore.setSelectedAnimation("No Animation");
    getViewerRuntime().animationController.current?.clear();

    useTreeViewStore.getState().clearTreeData();
    useModelState.getState().translation = null;
    if (!replacing) useSceneInfoStore.getState().setAvailableGroups([]); // drop streamed FEM/FEA groups

    // What was measured on the models goes with them (the Measure tab), and every highlight of
    // their objects.
    clearMeasurements();
    endAllSceneHighlights();

    // The models off the screen, through the viewport on screen. The three.js viewport's own
    // `clearModels` is this same three.js teardown, run here at once rather than after its lazy import.
    const viewport = activeViewport();
    if (viewport && viewport.kind !== "three") viewport.clearModels();
    clearThreeSceneModels();

    const ms = useModelState.getState();
    if (!replacing) ms.setModelUrl(null, null);
    // Every loaded source goes at once, so the storage list's checkboxes all uncheck, and whatever
    // follows the loaded sources (the load journal, the preview's lifetime) sees them go.
    ms.clearLoadedSources();
    // What the connection builder drew over its preview: its annotations, weld colours and marks,
    // and the preview's clash. Each also goes when the preview alone is unloaded (their own rule,
    // beside what draws them); here they go whatever that rule saw.
    clearConnectionOverlay();
    clearWeldMarks();
    clearWeldTint();
    forgetPreviewIfUnloaded(useModelState.getState().loadedSourceNames);
    // The selection held references to the meshes just removed: a later load would find them as
    // orphans ("N selected" with nothing highlighted).
    useSelectedObjectStore.getState().clearSelectedObjects();

    // The FEA streaming session is tied to the mesh just removed: the result controls flip back.
    clearActiveFeaStreaming();
    // The GLTF SimulationDataExtension panel reads this ref; left dangling it crashes when open.
    if (!replacing) getViewerRuntime().simulationData.current = null;

    if (!keepProcedural) {
        const cb = useCellBuilderStore.getState();
        if (cb.active) cb.close();
    }
    // The Sources tab's `loaded` mirror, now rather than when that tab next renders.
    getSingletonViewerStores().useAssetBrowserStore.getState().reconcileLoaded(new Set());

    // The on-demand render loop would not paint until the camera moves: the models stay as ghosts.
    requestRender();
}
