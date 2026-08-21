import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {getActiveFeaMesh} from "@/utils/scene/handlers/load_fea_streaming";
import {resetFeaAnimationPhase} from "@/utils/scene/fea/feaAnimationDriver";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {useLayoutStore} from "./layoutStore";
import {useModeStore} from "./modeStore";
import {animationControllerRef} from "@/state/refs";
import {useAnimationStore} from "@/state/animationStore";

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
/** Open the result data table, without closing it if it is already open. */
export function openDataTable(): void {
    const {mode} = useModeStore.getState();
    useLayoutStore.getState().openPanel(mode, "fea-table", "bottom");
}

export function toggleDataTable(): void {
    const {mode} = useModeStore.getState();
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

/**
 * Is there any animation at all — an FEA deformation sweep, or a GLTF clip?
 *
 * The transport used to require an FEA session, which left the toolbar's play and stop
 * greyed out for a GLTF model whose clips were perfectly playable. That was invisible
 * while the Simulation panel carried its own play button; with the panel gone it would
 * have meant no way to play a clip at all.
 */
export function anyAnimationActive(): boolean {
    if (useFeaAnimationStore.getState().sessionActive) return true;
    const names = animationControllerRef.current?.getAnimationNames() ?? [];
    return names.length > 0;
}

/** Start or stop whichever animation is live. */
export function togglePlay(): void {
    const s = useFeaAnimationStore.getState();
    if (s.sessionActive) {
        s.setIsPlaying(!s.isPlaying);
        return;
    }
    // GLTF clip: the mixer owns play state, so ask it rather than mirroring it here.
    animationControllerRef.current?.togglePlayPause();
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
    if (!s.sessionActive) {
        animationControllerRef.current?.stopAnimation();
        // The scrubber reads its position from the store, so a stop that only told the
        // mixer would leave the slider parked where the clip was when it stopped.
        useAnimationStore.getState().setCurrentKey(0);
        return;
    }
    s.setIsPlaying(false);
    s.setFactor(0);
    const mesh = getActiveFeaMesh();
    if (mesh?.morphTargetInfluences) mesh.morphTargetInfluences[0] = 0;
    resetFeaAnimationPhase();
}
