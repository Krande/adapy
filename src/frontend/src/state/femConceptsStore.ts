import {create} from "zustand";

import type {MassGlyph, BcGlyph, LoadScenario} from "@/extensions/design_and_analysis_extension";

// Holds the FEM-concept data parsed from the loaded model's ADA_EXT extension
// (masses + BCs + pre-resolved load scenarios) plus the viewer's per-category
// visibility + the selected load scenario. The FemConceptsController subscribes
// and rebuilds the glyph overlay; the FEM scene panel drives the toggles.
interface FemConceptsState {
    masses: MassGlyph[];
    bcs: BcGlyph[];
    scenarios: LoadScenario[];

    showMasses: boolean;
    showBcs: boolean;
    selectedScenario: number; // index into `scenarios`, or -1 for "none"

    setData: (d: {masses: MassGlyph[]; bcs: BcGlyph[]; scenarios: LoadScenario[]}) => void;
    setShowMasses: (v: boolean) => void;
    setShowBcs: (v: boolean) => void;
    setSelectedScenario: (i: number) => void;
}

export const useFemConceptsStore = create<FemConceptsState>((set) => ({
    masses: [],
    bcs: [],
    scenarios: [],
    showMasses: true,
    showBcs: true,
    selectedScenario: -1,

    setData: ({masses, bcs, scenarios}) =>
        set((s) => ({
            masses,
            bcs,
            scenarios,
            // Keep the current selection if still valid, else default to the
            // first scenario (or none when there are none).
            selectedScenario: scenarios.length
                ? s.selectedScenario >= 0 && s.selectedScenario < scenarios.length
                    ? s.selectedScenario
                    : 0
                : -1,
        })),
    setShowMasses: (showMasses) => set({showMasses}),
    setShowBcs: (showBcs) => set({showBcs}),
    setSelectedScenario: (selectedScenario) => set({selectedScenario}),
}));
