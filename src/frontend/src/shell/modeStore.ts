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

export const MODE_IDS = ["inspect", "results", "build", "convert"] as const;
export type ModeId = (typeof MODE_IDS)[number];

export interface ModeDef {
    id: ModeId;
    label: string;
    /** Icon registry name. */
    icon: "mode-inspect" | "mode-results" | "mode-build" | "mode-data" | "mode-convert";
    /** One line, shown in the mode switcher tooltip. Says what you DO here. */
    hint: string;
}

export const MODES: readonly ModeDef[] = [
    // Ordered as work flows, left to right: convert what you were given, author, examine,
    // then post-process results.
    //
    // There is no Library mode. Browsing files is not an ACTIVITY you switch into — it is
    // something you do briefly, in the middle of another activity, to open the thing you
    // are about to work on. Making it a mode meant leaving whatever you were doing to
    // find a file, which is backwards. The Files panel toggles from the rail instead, and
    // is available in every mode. That is how people describe their own work, so it is
    // learnable in a way "most-used first" is not.
    //
    // Build sits before Inspect because you cannot inspect what does not exist yet, and
    // it puts the two "look at what is there" modes — Inspect and Results — side by side.
    {
        id: "convert",
        label: "Convert",
        icon: "mode-convert",
        // Its own mode rather than a panel in the Library's dock, where it competed for
        // the same column as the file browser you pick sources from. Converting is a
        // different activity from browsing: you arrive with an intent ("get this STEP
        // into GLB"), not to look around.
        hint: "Turn files into other formats — CAD and FEA sources into viewable models",
    },
    {
        id: "build",
        label: "Build",
        icon: "mode-build",
        hint: "Author geometry — cells, equipment, systems, procedures",
    },
    {
        id: "inspect",
        label: "Inspect",
        icon: "mode-inspect",
        // Deliberately the base state rather than a specialisation: it owns no panel and
        // no tool the other modes lack. What it offers is the ABSENCE of the others'
        // apparatus — the model, the tree, properties, and nothing else on screen.
        hint: "The model on its own — selection, tree, properties, sections, quantities",
    },
    {
        id: "results",
        label: "Results",
        icon: "mode-results",
        hint: "Post-process FEA results — fields, deformation, playback, data table",
    },
] as const;

export const isModeId = (v: unknown): v is ModeId => MODE_IDS.includes(v as ModeId);

/**
 * The mode to land in when nothing else decides — a fresh session, or persisted state
 * naming a mode that no longer exists.
 *
 * Explicit rather than MODES[0], because that array's order is a presentation choice
 * (it reads left-to-right as data flows) and the fallback must not follow it. Reordering
 * the switcher once silently made the fallback the Library mode, which needs REST and is an empty
 * workspace on desktop — the worst possible place to strand someone whose layout blob
 * just failed to load.
 */
export const DEFAULT_MODE: ModeId = "inspect";

export const modeDef = (id: ModeId): ModeDef =>
    MODES.find((m) => m.id === id) ?? MODES.find((m) => m.id === DEFAULT_MODE)!;

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
            mode: DEFAULT_MODE,
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
