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
//     weld colours and marks of the joint it previewed and that preview's clash, every highlight,
//     and a clash's fading of the rest (what it faded is put back first);
//   * unless kept: loads still queued (they would arrive into the scene just cleared) and an open
//     procedural model (its proxies are part of what is in the scene);
//   * unless it is a replace: what plugins drew over the scene, their lines, labels, element
//     paints and line, point and image layers (`ctx.scene.addLines` ... `addImage`). Their handles
//     do nothing after (plugins/sceneLayers.ts). And the clash check's result with its markers and
//     detail model (`forgetResult`); its target and settings stay.
//
// A replace leaves the plugins' overlays (it used to be that every clear did: "What stays: a
// plugin's own line, point and image layers"). A replace is a load, not a clear anyone asked for:
// a plugin's own `loadModel` replaces by default, and a renderer switch hands the models over
// through one; taking what the plugin drew there would undo the plugin's own work. What is pinned
// to an FEA result still goes with that result.
//
// What is drawn over the scene is the overlay manager's (viewport/api/overlayManager.ts): ONE call,
// `sceneOverlays.clear`, applies each overlay's own lifetime and replace policy, so an overlay that
// registers there cannot be missed by a clear. A guard test holds that what draws over the scene
// draws through it.
//
// Then every plugin listening (`ctx.scene.onSceneCleared`) is told, once, with what was done.

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
import {notifySceneCleared} from "@/plugins/sceneCleared";
import {forgetPreviewIfUnloaded} from "@/services/devConnections/previewOnShow";
import {clearJointIsolation} from "@/utils/scene/clashIsolation";
import {clearThreeSceneModels} from "@/utils/scene/clearThreeSceneModels";
import {clearConnectionOverlay} from "@/utils/scene/connectionOverlay";
import {clearWeldMarks} from "@/utils/scene/connectionWeldMarks";
import {clearWeldTint} from "@/utils/scene/connectionWeldTint";
import {sceneOverlays} from "@/viewport/api/overlayManager";
import {activeViewport} from "@/viewport/api/viewportApi";
import {clearActiveFeaStreaming} from "./load_fea_streaming";

export interface ClearSceneOptions {
    /** Who asked (a scope change, a replace, ...): for whoever reads a call of it, and handed to the
     *  plugins' `onSceneCleared` listeners; not acted on. */
    reason?: string;
    /** A load is about to put a model in place of what is cleared (`replace_model`, the
     *  three.js-free viewport's replace). What that load sets itself is left to it: the model URL
     *  that started it, the groups a streamed result announces before its mesh arrives, the GLTF
     *  simulation data. And what the plugins drew over the scene stays (see the header). */
    replacing?: boolean;
    /** Keep the loads still queued: they are what comes next (a replace from the queue, the gallery,
     *  a re-convert, unloading one streamed result). Dropped otherwise. */
    keepQueuedLoads?: boolean;
    /** Keep an open procedural model. Closed otherwise. */
    keepProcedural?: boolean;
    /** Keep the loaded sources, their model session and lineage: for a replace whose viewport swaps
     *  the model in place itself (the three.js-free viewport's replace), which reads them to put the
     *  new model where the old one was. Ended here first, the model shown again was drawn only in
     *  part (adapy 0.161.3, the three.js-free viewport undo test). */
    keepLoadedSources?: boolean;
}

export async function clearScene(options: ClearSceneOptions = {}): Promise<void> {
    const {replacing = false, keepQueuedLoads = false, keepProcedural = false, keepLoadedSources = false} = options;

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
    // What is drawn over the scene, each overlay as its lifetime and replace policy say: the
    // highlights of the models' objects, what plugins drew (a replace leaves that).
    sceneOverlays.clear({replacing});
    // A clash's "fade rest" / "hide rest", put back while its models are still there to put back.
    clearJointIsolation();

    // The models off the screen, through the viewport on screen. The three.js viewport's own
    // `clearModels` is this same three.js teardown, run here at once rather than after its lazy import.
    const viewport = activeViewport();
    if (viewport && viewport.kind !== "three") viewport.clearModels();
    clearThreeSceneModels();

    const ms = useModelState.getState();
    if (!replacing) ms.setModelUrl(null, null);
    // Every loaded source goes at once, so the storage list's checkboxes all uncheck, and whatever
    // follows the loaded sources (the load journal, the preview's lifetime) sees them go.
    if (!keepLoadedSources) ms.clearLoadedSources();
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

    // The clash check's result and the markers drawn from it (they follow the store, not the
    // models, so they outlived them); the check's target and settings stay. A replace keeps it: a
    // renderer switch hands the same models over through one.
    if (!replacing) {
        const {useClashCheckStore} = await import("@/state/clashCheckStore");
        useClashCheckStore.getState().forgetResult();
    }

    // The on-demand render loop would not paint until the camera moves: the models stay as ghosts.
    requestRender();

    // Last, so a plugin that draws again from its listener draws into the scene as cleared.
    notifySceneCleared({reason: options.reason ?? null, replacing, overlaysRemoved: !replacing});
}
