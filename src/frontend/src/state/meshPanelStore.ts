import {create} from "zustand";
import {persist} from "zustand/middleware";
import {DEFAULT_SPIKE_THRESHOLDS} from "@/utils/mesh_select/meshStats";

// Editable spike thresholds for the Scene panel's "Mesh" mode (Scene dropdown → Mesh). The section
// scans every geom in the scene for "crows-nest" tessellation spikes (see meshStats.ts) and lists
// the offenders in a distortion-sorted table; these two thresholds let a scan be re-run tighter or
// looser than the gallery's fixed defaults, and persist as a viewing preference. Visibility is owned
// by sceneInfoStore (mode === "mesh"); scan RESULTS are transient in the section component.
interface MeshPanelState {
    spikeAspectMin: number;
    spikeOutlierK: number;
    setSpikeAspectMin: (v: number) => void;
    setSpikeOutlierK: (v: number) => void;
    resetThresholds: () => void;
}

export const useMeshPanelStore = create<MeshPanelState>()(
    persist(
        (set) => ({
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
            partialize: (s) => ({
                spikeAspectMin: s.spikeAspectMin,
                spikeOutlierK: s.spikeOutlierK,
            }),
        },
    ),
);
