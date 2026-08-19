import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useTableNavStore} from "@/state/tableNavStore";
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
    useLayoutStore.getState().togglePanel(mode, "fea-table", "bottom");
    // Keep the legacy flag in step: SimulationDataInfoPanel gates on it, and other
    // callers (the Properties panel's "Show in data") still set it directly.
    const nav = useTableNavStore.getState();
    nav.setPanelOpen(!nav.isPanelOpen);
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
