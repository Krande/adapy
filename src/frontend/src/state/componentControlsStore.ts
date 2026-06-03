// Panel state for the ComponentControls UI: visibility, currently-
// selected ConnectionSpec, the user's form inputs (per-role dict
// mirroring spec_to_form_schema), and the in-flight build job id.
//
// Pure UI state — fetched specs / job records live in
// componentBuildStore and are queried via viewerApi.

import {create} from "zustand";

/** Per-role inputs the form collects. Mirrors the dict shape
 *  ``ada.api.connections.sample.build_sample`` accepts on the
 *  backend: keys are lowercase role names ("incoming", "landing"),
 *  values carry at minimum a ``section`` and (when the role has an
 *  angle_range) an ``angle_deg``. */
export type ComponentInputs = Record<string, Record<string, unknown>>;

type ComponentControlsState = {
    isVisible: boolean;
    setIsVisible: (v: boolean) => void;
    toggleVisible: () => void;

    selectedSpecName: string | null;
    /** Replace the selected spec and reset inputs to ``defaults``. Pass
     *  null to clear the selection. */
    selectSpec: (
        specName: string | null,
        defaults?: ComponentInputs | null,
    ) => void;

    /** Inputs keyed by role (lowercase). Updated incrementally as the
     *  user tweaks each role's section / angle in the form. */
    inputs: ComponentInputs;
    setInputs: (inputs: ComponentInputs) => void;
    /** Set a single field on a single role without touching the rest.
     *  Use for "user typed an angle" / "user picked a section" — the
     *  diff stays small so React re-renders are cheap. */
    setRoleField: (role: string, field: string, value: unknown) => void;

    /** The most recent build job kicked off from this panel. The
     *  pipeline subscribes to this to start polling. Null between
     *  builds. */
    currentJobId: string | null;
    setCurrentJobId: (jobId: string | null) => void;
};

export const useComponentControlsStore = create<ComponentControlsState>((set, get) => ({
    isVisible: false,
    setIsVisible: (isVisible) => set({isVisible}),
    toggleVisible: () => set({isVisible: !get().isVisible}),

    selectedSpecName: null,
    selectSpec: (specName, defaults) =>
        set({selectedSpecName: specName, inputs: defaults ? {...defaults} : {}}),

    inputs: {},
    setInputs: (inputs) => set({inputs}),
    setRoleField: (role, field, value) =>
        set((s) => ({
            inputs: {
                ...s.inputs,
                [role]: {...(s.inputs[role] ?? {}), [field]: value},
            },
        })),

    currentJobId: null,
    setCurrentJobId: (currentJobId) => set({currentJobId}),
}));
