import {create} from "zustand";
import {persist} from "zustand/middleware";
import {DEFAULT_SPIKE_THRESHOLDS} from "@/utils/mesh_select/meshStats";

// State for the dedicated "Mesh" inspection panel (Menu bar → Mesh). The panel scans every geom in
// the scene for "crows-nest" tessellation spikes (see meshStats.ts) and lists the offenders in a
// distortion-sorted table. The two spike thresholds are editable here so a scan can be re-run
// tighter or looser than the gallery's fixed defaults; they persist as a viewing preference. The
// scan RESULTS are transient and live in the panel component, not the store.
interface MeshPanelState {
    visible: boolean;
    setVisible: (v: boolean) => void;
    toggle: () => void;

    // Editable spike thresholds (defaults mirror meshStats' module constants).
    spikeAspectMin: number;
    spikeOutlierK: number;
    setSpikeAspectMin: (v: number) => void;
    setSpikeOutlierK: (v: number) => void;
    resetThresholds: () => void;
}

export const useMeshPanelStore = create<MeshPanelState>()(
    persist(
        (set) => ({
            visible: false,
            setVisible: (visible) => set({visible}),
            toggle: () => set((s) => ({visible: !s.visible})),

            spikeAspectMin: DEFAULT_SPIKE_THRESHOLDS.spikeAspectMin,
            spikeOutlierK: DEFAULT_SPIKE_THRESHOLDS.spikeOutlierK,
            setSpikeAspectMin: (spikeAspectMin) => set({spikeAspectMin}),
            setSpikeOutlierK: (spikeOutlierK) => set({spikeOutlierK}),
            resetThresholds: () =>
                set({
                    spikeAspectMin: DEFAULT_SPIKE_THRESHOLDS.spikeAspectMin,
                    spikeOutlierK: DEFAULT_SPIKE_THRESHOLDS.spikeOutlierK,
                }),
        }),
        {
            name: "ada-mesh-panel",
            // Persist the editable thresholds; visibility is a per-session UI state we still keep so
            // the panel re-opens where the user left it.
            partialize: (s) => ({
                visible: s.visible,
                spikeAspectMin: s.spikeAspectMin,
                spikeOutlierK: s.spikeOutlierK,
            }),
        },
    ),
);
