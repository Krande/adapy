import {create} from "zustand";
import {persist} from "zustand/middleware";

// ============================================================================
// THE NON-MODALITY CONTRACT
//
// Blender's first design paradigm: you indicate WHAT DATA you work on, then WHAT YOU
// WANT TO DO. There is no tool mode you must enter before you are allowed to act.
// Modes here change which TOOLS ARE OFFERED. They never change what exists, what is
// selected, or what you may inspect.
//
// setMode() must NOT:
//   * change selection, camera, visibility, hidden ranges, clipping planes or loaded
//     models — no three.js state whatsoever;
//   * unmount a headless controller. CellBuilderController, SectionPlanesController,
//     FemConceptsController, TypeIconController and ProceduralFollowerController mount
//     ONCE, in every mode, inside the viewport host. A cellbuilder edit must survive a
//     trip to Results mode and back;
//   * gate what you can select or inspect. Clicking a cellbuilder cell while in Results
//     mode fills the Properties panel with that cell. Properties is mode-independent;
//   * hide a panel the user pinned (sticky panels survive mode switches);
//   * remove a global shortcut. All 12 bindings in setupCameraControlsHandlers fire in
//     every mode;
//   * fire by itself. Loading a FEA model puts a BADGE on the Results button; it does
//     not jump you there. Auto-switching is the most-violated Blender principle in DCC
//     clones and the fastest way to make a UI feel like it is fighting you.
//
// Tool shortcuts (G/R/S, X/Y/Z) stay scoped to the ACTIVE TOOL, not the mode — they
// key off `cellBuilderStore.active !== null` today and must keep doing so.
//
// Enforced by src/__tests__/shell/modeSemantics.test.ts, which snapshots the viewer
// stores either side of a setMode() and asserts they are byte-identical.
// ============================================================================

export const MODE_IDS = ["inspect", "results", "build", "data"] as const;
export type ModeId = (typeof MODE_IDS)[number];

export interface ModeDef {
    id: ModeId;
    label: string;
    /** Icon registry name. */
    icon: "mode-inspect" | "mode-results" | "mode-build" | "mode-data";
    /** One line, shown in the mode switcher tooltip. Says what you DO here. */
    hint: string;
}

export const MODES: readonly ModeDef[] = [
    {
        id: "inspect",
        label: "Inspect",
        icon: "mode-inspect",
        hint: "Navigate and interrogate the model — selection, tree, sections, quantities",
    },
    {
        id: "results",
        label: "Results",
        icon: "mode-results",
        hint: "Post-process FEA results — fields, deformation, playback, data table",
    },
    {
        id: "build",
        label: "Build",
        icon: "mode-build",
        hint: "Author geometry — cells, equipment, systems, procedures",
    },
    {
        id: "data",
        label: "Data",
        icon: "mode-data",
        hint: "Move data in and out — storage, conversion, jobs, administration",
    },
] as const;

export const isModeId = (v: unknown): v is ModeId => MODE_IDS.includes(v as ModeId);

export const modeDef = (id: ModeId): ModeDef => MODES.find((m) => m.id === id) ?? MODES[0];

interface ModeState {
    mode: ModeId;
    /**
     * Modes with something newly worth looking at — a FEA deck finished loading, a
     * conversion failed. Rendered as a dot on the mode button. Deliberately passive:
     * this is how the shell tells you something happened WITHOUT taking you there.
     */
    badges: Partial<Record<ModeId, number | "dot">>;

    setMode: (mode: ModeId) => void;
    setBadge: (mode: ModeId, badge: number | "dot" | null) => void;
    clearBadge: (mode: ModeId) => void;
}

export const useModeStore = create<ModeState>()(
    persist(
        (set) => ({
            // Inspect: the one thing every persona does, and the only mode that is
            // useful with nothing loaded.
            mode: "inspect",
            badges: {},

            setMode: (mode) =>
                set((s) => {
                    if (s.mode === mode) return s;
                    // Entering a mode acknowledges its badge. Nothing else changes —
                    // see the contract above.
                    const badges = {...s.badges};
                    delete badges[mode];
                    return {mode, badges};
                }),

            setBadge: (mode, badge) =>
                set((s) => {
                    const badges = {...s.badges};
                    if (badge == null) delete badges[mode];
                    else badges[mode] = badge;
                    return {badges};
                }),

            clearBadge: (mode) =>
                set((s) => {
                    if (!(mode in s.badges)) return s;
                    const badges = {...s.badges};
                    delete badges[mode];
                    return {badges};
                }),
        }),
        {
            name: "ada:mode:v1",
            // Badges describe this session's events; restoring them would show a dot
            // for a conversion that finished last week.
            partialize: (s) => ({mode: s.mode}),
            migrate: (persisted) => {
                const p = persisted as {mode?: unknown} | undefined;
                return {mode: isModeId(p?.mode) ? p.mode : "inspect"} as never;
            },
        },
    ),
);
