import {create} from "zustand";

import type {
    MassGlyph,
    BcGlyph,
    ConstraintGlyph,
    EquipmentGlyph,
    LoadScenario,
} from "@/extensions/design_and_analysis_extension";
import {refKey, toggleRef, type FemConceptRef} from "@/utils/fem/femSelection";
import {defaultEquipmentGroups, equipmentGroups} from "@/utils/fem/femTakeoff";

// Holds the FEM-concept data parsed from the loaded model's ADA_EXT extension
// (masses + BCs + constraints + pre-resolved load scenarios + placed equipment) plus the
// viewer's per-category visibility + the selected load scenario. The FemConceptsController
// subscribes and rebuilds the glyph overlay; the FEM scene panel drives the toggles, and its
// take-off weighs what they switch on.
interface FemConceptsState {
    masses: MassGlyph[];
    bcs: BcGlyph[];
    constraints: ConstraintGlyph[];
    scenarios: LoadScenario[];
    equipment: EquipmentGlyph[];

    showMasses: boolean;
    showBcs: boolean;
    showEquipment: boolean;
    /** Per equipment group (see `equipmentGroups`), whether it is drawn and weighed. */
    equipmentGroupOn: Record<string, boolean>;
    /** Whether a scenario's arrows include the loads the analysis model generated from equipment. */
    showGeneratedLoads: boolean;
    /** Whether the take-off counts the model's own (structural) mass. */
    takeoffModel: boolean;
    selectedScenario: number; // index into `scenarios`, or -1 for "none"
    selectedConstraint: number; // index into `constraints`, or -1 for "none"
    /** The concepts picked in the view or the panel; the Selected Object Info panel describes them. */
    selection: FemConceptRef[];

    setData: (d: {
        masses: MassGlyph[];
        bcs: BcGlyph[];
        constraints?: ConstraintGlyph[];
        scenarios: LoadScenario[];
        equipment?: EquipmentGlyph[];
    }) => void;
    setShowMasses: (v: boolean) => void;
    setShowBcs: (v: boolean) => void;
    setShowEquipment: (v: boolean) => void;
    setEquipmentGroupOn: (key: string, on: boolean) => void;
    setShowGeneratedLoads: (v: boolean) => void;
    setTakeoffModel: (v: boolean) => void;
    /** Switch the overlays on -- what loading a model built for analysis asks for. */
    showAll: () => void;
    setSelectedScenario: (i: number) => void;
    setSelectedConstraint: (i: number) => void;
    /** Select `ref`; `additive` toggles it in or out of the selection instead of replacing it. */
    selectConcept: (ref: FemConceptRef, additive?: boolean) => void;
    /** Select all of `refs` (replacing, or added to the selection when `additive`). */
    selectConcepts: (refs: FemConceptRef[], additive?: boolean) => void;
    clearConceptSelection: () => void;
}

export const useFemConceptsStore = create<FemConceptsState>((set) => ({
    masses: [],
    bcs: [],
    constraints: [],
    scenarios: [],
    equipment: [],
    showMasses: false,
    showBcs: false,
    showEquipment: false,
    equipmentGroupOn: {},
    showGeneratedLoads: true,
    takeoffModel: true,
    selectedScenario: -1,
    selectedConstraint: -1,
    selection: [],

    setData: ({masses, bcs, constraints = [], scenarios, equipment = []}) =>
        set((s) => {
            // A group keeps the switch the user gave it; a new one starts at its default.
            const defaults = defaultEquipmentGroups(equipmentGroups(equipment), scenarios);
            const equipmentGroupOn: Record<string, boolean> = {};
            for (const key of Object.keys(defaults)) equipmentGroupOn[key] = s.equipmentGroupOn[key] ?? defaults[key];
            // A selection indexes the arrays being replaced: it means something else now, so it goes.
            const unchanged =
                s.masses === masses && s.bcs === bcs && s.constraints === constraints &&
                s.scenarios === scenarios && s.equipment === equipment;
            return {
                selection: unchanged ? s.selection : [],
                masses,
                bcs,
                constraints,
                scenarios,
                equipment,
                equipmentGroupOn,
                // Keep the current selection if still valid, else default to
                // "none" — FEM concepts start hidden and the user opts in.
                selectedScenario:
                    s.selectedScenario >= 0 && s.selectedScenario < scenarios.length
                        ? s.selectedScenario
                        : -1,
                selectedConstraint:
                    s.selectedConstraint >= 0 && s.selectedConstraint < constraints.length
                        ? s.selectedConstraint
                        : -1,
            };
        }),
    setShowMasses: (showMasses) => set({showMasses}),
    setShowBcs: (showBcs) => set({showBcs}),
    setShowEquipment: (showEquipment) => set({showEquipment}),
    setEquipmentGroupOn: (key, on) => set((s) => ({equipmentGroupOn: {...s.equipmentGroupOn, [key]: on}})),
    setShowGeneratedLoads: (showGeneratedLoads) => set({showGeneratedLoads}),
    setTakeoffModel: (takeoffModel) => set({takeoffModel}),
    showAll: () => set({showMasses: true, showBcs: true, showEquipment: true}),
    // A load is selected inside its scenario; picking another scenario drops it (its arrow is gone).
    setSelectedScenario: (selectedScenario) =>
        set((s) => ({
            selectedScenario,
            selection: s.selection.filter((r) => r.kind !== "load" || r.scenario === selectedScenario),
        })),
    setSelectedConstraint: (selectedConstraint) =>
        set((s) => ({
            selectedConstraint,
            selection: s.selection.filter((r) => r.kind !== "constraint" || r.index === selectedConstraint),
        })),
    selectConcept: (ref, additive = false) =>
        set((s) => ({selection: additive ? toggleRef(s.selection, ref) : [ref]})),
    selectConcepts: (refs, additive = false) =>
        set((s) => {
            if (!additive) return {selection: [...refs]};
            const have = new Set(s.selection.map(refKey));
            return {selection: [...s.selection, ...refs.filter((r) => !have.has(refKey(r)))]};
        }),
    clearConceptSelection: () => set((s) => (s.selection.length ? {selection: []} : {})),
}));
