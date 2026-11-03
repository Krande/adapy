import {create} from "zustand";
import {persist} from "zustand/middleware";

import {simTabKey, type RememberedSimTab} from "@/components/simulation/simPanelTabs";
import {useAnimationStore} from "./animationStore";
import {useFeaAnimationStore} from "./feaAnimationStore";

// The Simulation panel's last picked tab, kept outside the panel so it survives
// closing it and switching between docked, floating and window. Persisted.
interface SimPanelState {
    remembered: RememberedSimTab | null;
    remember: (tab: string, key: string) => void;
}

export const useSimPanelStore = create<SimPanelState>()(
    persist(
        (set) => ({
            remembered: null,
            remember: (tab, key) => set({remembered: {tab, key}}),
        }),
        {name: "ada-sim-panel-tab", partialize: (s) => ({remembered: s.remembered})},
    ),
);

/** Does the loaded scene have results the results tab can drive? */
export function sceneHasResults(): boolean {
    return useFeaAnimationStore.getState().sessionActive || useAnimationStore.getState().hasAnimation;
}

/** The key the current scene's tab pick is remembered under. */
export function currentSimTabKey(): string {
    return simTabKey(useFeaAnimationStore.getState().sourceName, sceneHasResults());
}

/** Open the Simulation panel on `tab`. */
export function openSimulationPanel(tab: string): void {
    useSimPanelStore.getState().remember(tab, currentSimTabKey());
    const anim = useAnimationStore.getState();
    if (!anim.isControlsVisible) anim.setIsControlsVisible(true);
}
