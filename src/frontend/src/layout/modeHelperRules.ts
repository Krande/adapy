// The classic UI's mode helper, as rules: which of its three headings are lit for what is on
// screen, and how its topology dropdown maps onto the procedural builder's view state. The
// helper switches no mode (the classic UI has none); it only shows what is there. No React
// here, so `node --test` can hold the rules.

import {hasEmbeddedDoc, useCellBuilderStore} from "@/state/cellBuilderStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useModeHelperStore} from "@/state/modeHelperStore";
import {feModelLoaded, hasFemConcepts} from "@/components/simulation/simPanelTabs";

import {servedClassicShell} from "./presets/classicShell";

/** The command that shows or hides the helper (kept here, not in `CORE_COMMAND_IDS`, which the facade root exports). */
export const MODE_HELPER_COMMAND_ID = "core:toggle-mode-helper";

export type HelperMode = "topology" | "fem" | "construction";

/** The headings, in the order they are drawn. */
export const HELPER_MODES: readonly {id: HelperMode; label: string}[] = [
  {id: "topology", label: "Topology"},
  {id: "fem", label: "FEM"},
  {id: "construction", label: "Construction"},
];

/** What the three headings read: plain values, so the rule is testable. */
export interface HelperInputs {
  /** A procedural model is open in the cell builder (an editable session, or one read off a loaded scene). */
  builderLoaded: boolean;
  /** The builder holds a compiled simulation result (its FE model), shown. */
  simulationLoaded: boolean;
  /** The builder holds a loaded detail result, shown. */
  detailLoaded: boolean;
  /** The FEA manifest (a mesh through the FEA path, with or without results). */
  manifest: unknown;
  /** A result session is active. */
  sessionActive: boolean;
  /** The FE-concepts store holds something. */
  femHasConcepts: boolean;
}

/**
 * Which headings are lit; several may be at once.
 *  - Topology: a procedural model is open in the cell builder.
 *  - FEM: core's own rule for "an FE model or result is loaded" (`feModelLoaded`, the one that offers the
 *    Simulation panel's Model tab) or an active result session; also the builder's compiled simulation
 *    result (`resultSourceName`), which is the FE model drawn.
 *  - Construction: the fabrication / detail model is shown. The builder keeps no flag of its own for it; its
 *    detail result being loaded (`detailSourceName`, which the builder drops again when another representation
 *    is picked) is the most faithful signal. The detail representation merely being selected (`repMode`) is not
 *    used: until the result has compiled, nothing is on screen, and a failed compile leaves it selected.
 */
export function activeModes(i: HelperInputs): ReadonlySet<HelperMode> {
  const on = new Set<HelperMode>();
  if (i.builderLoaded) on.add("topology");
  if (feModelLoaded({manifest: i.manifest, femHasConcepts: i.femHasConcepts}) || i.sessionActive || i.simulationLoaded) on.add("fem");
  if (i.detailLoaded) on.add("construction");
  return on;
}

/** The inputs, read from the stores now. */
export function readHelperInputs(): HelperInputs {
  const cb = useCellBuilderStore.getState();
  const fea = useFeaAnimationStore.getState();
  return {
    builderLoaded: cb.active !== null || hasEmbeddedDoc(cb),
    simulationLoaded: cb.resultSourceName !== null,
    detailLoaded: cb.detailSourceName !== null,
    manifest: fea.manifest,
    sessionActive: fea.sessionActive,
    femHasConcepts: hasFemConcepts(useFemConceptsStore.getState()),
  };
}

/** The topology input / output comparison, as the dropdown offers it. */
export type Comparison = "side-by-side" | "overlay" | "off";

export const COMPARISONS: readonly {id: Comparison; label: string}[] = [
  {id: "side-by-side", label: "Side by side"},
  {id: "overlay", label: "Overlay"},
  {id: "off", label: "Off"},
];

/** The dropdown's value for the builder's two flags (they are independent in the store; beside wins). */
export function comparisonOf(s: {superimpose: boolean; sideBySide: boolean}): Comparison {
  return s.sideBySide ? "side-by-side" : s.superimpose ? "overlay" : "off";
}

/** The slice of the builder store the dropdown drives (`state/cellbuilder/viewStateSlice`). */
export interface ComparisonTarget {
  repMode: "topology" | "simulation" | "detail";
  setSuperimpose(on: boolean): Promise<void>;
  setSideBySide(on: boolean): void;
  setRepMode(mode: "simulation"): Promise<void>;
}

/**
 * Make the builder show `value`, through the same actions as the `builderSuperimpose` and `builderSideBySide`
 * commands, turning the other one off so exactly one comparison is on. From the plain topology view a
 * comparison needs a result to compare with, so the simulation view is opened (as `setSuperimpose` does for the overlay).
 */
export async function applyComparison(value: Comparison, s: ComparisonTarget): Promise<void> {
  if (value === "off") {
    s.setSideBySide(false);
    await s.setSuperimpose(false);
    return;
  }
  if (value === "overlay") {
    s.setSideBySide(false);
    await s.setSuperimpose(true);
    return;
  }
  await s.setSuperimpose(false);
  s.setSideBySide(true);
  if (s.repMode === "topology") await s.setRepMode("simulation");
}

/** Offered in core's own UI only: a composed shell (the Desktop UI, a plugin's) is served a shell, core's own is not. */
export function modeHelperOffered(): boolean {
  return servedClassicShell() === null;
}

/** Show or hide the helper (the command's and the shortcut's action); false where it is not offered. */
export function toggleModeHelper(): boolean {
  if (!modeHelperOffered()) return false;
  useModeHelperStore.getState().toggle();
  return true;
}
