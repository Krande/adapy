import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {getActiveFeaMesh} from "@/utils/scene/handlers/load_fea_streaming";
import {resetFeaAnimationPhase} from "@/utils/scene/fea/feaAnimationDriver";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {useLayoutStore} from "./layoutStore";
import {useModeStore} from "./modeStore";

// Results-mode rail actions.
//
// Same discipline as inspectActions: these DELEGATE to the stores the Simulation panel
// already drives. The rail is a second entry point, never a second implementation, so a
// change to playback behaviour cannot leave the rail behind.

/** Toggle the colour legend HUD. */
export function toggleLegend(): void {
    const s = useColorStore.getState();
    s.setShowLegend(!s.showLegend);
}

/** Show or hide the result data table in the bottom dock. */
export function toggleDataTable(): void {
    const {mode} = useModeStore.getState();
    // The legacy isPanelOpen flag follows automatically via useLegacyFlagSync, so this
    // only has to move the layout.
    useLayoutStore.getState().togglePanel(mode, "fea-table", "bottom");
}

/** Reveal the FEM concepts view (masses / BCs / load scenarios). */
export function openFemConcepts(): void {
    useSceneInfoStore.getState().setMode("fem");
    const {mode} = useModeStore.getState();
    useLayoutStore.getState().openPanel(mode, "scene", "right");
}

/** Is a result session live? Used to disable playback actions honestly. */
export function feaSessionActive(): boolean {
    return useFeaAnimationStore.getState().sessionActive;
}

/** Start or stop the deformation sweep. */
export function togglePlay(): void {
    const s = useFeaAnimationStore.getState();
    s.setIsPlaying(!s.isPlaying);
}

/**
 * Stop and reset the deformation to zero.
 *
 * The same three steps the Simulation panel's Stop does, in the same order: pause, zero
 * the factor, zero the mesh's morph influence, reset the driver's phase.
 *
 * The morph write is not redundant with setFactor(0): the RAF driver only applies the
 * factor while PLAYING, so a stop that only touched the store would leave the mesh frozen
 * at whatever deflection it was showing — the numbers would say zero and the model would
 * disagree.
 */
export function stopPlayback(): void {
    const s = useFeaAnimationStore.getState();
    s.setIsPlaying(false);
    s.setFactor(0);
    const mesh = getActiveFeaMesh();
    if (mesh?.morphTargetInfluences) mesh.morphTargetInfluences[0] = 0;
    resetFeaAnimationPhase();
}
