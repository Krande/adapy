// Core's modes: the activities a UI with modes switches between, as typed constants, and what a
// mode does -- its behaviour -- which each UI assigns for itself.
//
// THE VOCABULARY IS CORE'S. A mode is named by its constant (`Mode.FemResults`), never by a string
// typed at the call site: the strings exist only where a mode is stored (a `?mode=` link, the
// remembered mode, a saved layout). They are grouped under headings (`ModeGroup`): Topology, Fem,
// Construction. A plugin's mode (`PluginModeSpec`) joins a group with `group`.
//
// Construction is a chain of phases, in work order: Fabrication-cad, Robot-sim (weld) -- the robot
// cell, from the stock material through cutting to welding -- and Installation-sim -- the assembly
// cell.
//
// THE BEHAVIOUR IS EACH UI'S. Core's own (classic) UI has no modes, so nothing here ever applies to
// it. A UI with modes (a composed shell: the Desktop UI, a plugin's) lists the modes it offers in
// `UiShellLayout.modes` and gives each its `ModeBehaviour`. Core reacts to a mode ONLY through the
// active mode's declared behaviour: it never compares mode ids, and never infers what a UI meant from
// what is drawn.
//
// Dependency-free (the facade root exports it).

/** Core's modes. The value is the id stored in links, preferences and layouts. */
export const Mode = {
  /** The procedural topology: cells, equipment, systems; edited in the cell builder. */
  TopologyInput: "topology-input",
  /** The compiled CAD model: properties, selection, tree, sections, quantities. */
  TopologyOutput: "topology-output",
  /** The FE model, meshed: elements, groups, properties -- no results. */
  FemModel: "fem-model",
  /** FEA results: fields, deformation, playback, the data table. */
  FemResults: "fem-results",
  /** The fabrication model: the compile's detailed CAD. */
  ConstructionFabrication: "construction-fabrication",
  /**
   * The robot cell: a simulation of the parts from the stock material through cutting to welding,
   * its runs and their replay.
   */
  ConstructionRobotSim: "construction-robot-sim",
  /**
   * The assembly cell: a simulation of putting the welded parts together with a crane or a large
   * handler, and welding the assembly.
   */
  ConstructionInstallationSim: "construction-installation-sim",
} as const;

export type CoreModeId = (typeof Mode)[keyof typeof Mode];

/** A mode's id: one of core's, or a plugin's (`PluginModeSpec.id`). */
export type ModeId = CoreModeId | (string & {});

/** The headings modes are grouped under. A heading is a label, not a mode. */
export const ModeGroup = {
  Topology: "topology",
  Fem: "fem",
  Construction: "construction",
} as const;

export type ModeGroupId = (typeof ModeGroup)[keyof typeof ModeGroup];

/** What core knows about one of its modes: where it sits and what it is called by default. */
export interface CoreModeDef {
  id: CoreModeId;
  group: ModeGroupId;
  /** The default label (a UI may give its own). */
  label: string;
  /** What you do there, in one line. */
  hint: string;
  /** Switcher order: work flows left to right. */
  order: number;
  /**
   * What the mode does when the UI offering it declares nothing for a field (`ModeBehaviour`):
   * a UI's `behaviour` wins field by field. Most modes have none, and keep core's defaults.
   */
  behaviour?: ModeBehaviour;
}

/** The domain the robot and installation simulations' providers are listed under. */
export const ROBOTICS_SIMULATION_DOMAIN = "robotics";

/**
 * A simulation cell's default behaviour: the procedural model put away, the scene coloured by the
 * mode's own content (no FEA field), undeformed, every loaded model drawn (the replayed cell and
 * the subject it works on), and the Simulations panel listing the robotics providers, the ones that
 * declare the mode first.
 */
const simulationCell = (empty: string): ModeBehaviour => ({
  builder: "away",
  sceneColor: "own",
  deformation: false,
  simulations: { domain: ROBOTICS_SIMULATION_DOMAIN, empty },
});

export const CORE_MODES: readonly CoreModeDef[] = [
  { id: Mode.TopologyInput, group: ModeGroup.Topology, label: "Input", order: 10, hint: "The procedural topology: cells, equipment, systems; compile it into Output" },
  { id: Mode.TopologyOutput, group: ModeGroup.Topology, label: "Output", order: 20, hint: "The compiled CAD model: properties, selection, tree, sections, quantities" },
  { id: Mode.FemModel, group: ModeGroup.Fem, label: "Model", order: 30, hint: "The FE model, meshed: elements, groups, properties — no results" },
  { id: Mode.FemResults, group: ModeGroup.Fem, label: "Results", order: 40, hint: "Post-process FEA results — fields, deformation, playback, data table" },
  { id: Mode.ConstructionFabrication, group: ModeGroup.Construction, label: "Fabrication-cad", order: 60, hint: "The fabrication model: the compile's detailed CAD" },
  {
    id: Mode.ConstructionRobotSim,
    group: ModeGroup.Construction,
    label: "Robot-sim (weld)",
    order: 70,
    hint: "The robot cell: the stock material, cutting and welding the parts with robots; follow the runs, replay one",
    behaviour: simulationCell("No robot cell simulation provider yet."),
  },
  {
    id: Mode.ConstructionInstallationSim,
    group: ModeGroup.Construction,
    label: "Installation-sim",
    order: 80,
    hint: "The assembly cell: put the welded parts together with a crane or handler, and weld the assembly",
    behaviour: simulationCell("No installation simulation provider yet."),
  },
];

/** The headings, in switcher order, with their labels. */
export const MODE_GROUPS: readonly { id: ModeGroupId; label: string }[] = [
  { id: ModeGroup.Topology, label: "Topology" },
  { id: ModeGroup.Fem, label: "Fem" },
  { id: ModeGroup.Construction, label: "Construction" },
];

/** Is `id` one of core's modes. */
export function isCoreMode(id: string | null | undefined): id is CoreModeId {
  return !!id && CORE_MODES.some((m) => m.id === id);
}

/** Core's definition of one of its modes; undefined for any other id. */
export function coreModeDef(id: string | null | undefined): CoreModeDef | undefined {
  return id ? CORE_MODES.find((m) => m.id === id) : undefined;
}

/**
 * A mode's behaviour: the UI's declared fields over core's default for the mode
 * (`CoreModeDef.behaviour`), field by field. Undefined when neither says anything.
 */
export function withCoreBehaviour(id: string, declared: ModeBehaviour | undefined): ModeBehaviour | undefined {
  const core = coreModeDef(id)?.behaviour;
  if (!core) return declared;
  return { ...core, ...declared };
}

// ---------------------------------------------------------------------------
// Behaviour: what a mode does, assigned by each UI per mode.
// ---------------------------------------------------------------------------

/**
 * What the cell builder is in a mode.
 *
 * - `"edit"`: the procedural model is worked on here. Its picks are in Properties and the builder's
 *   panel is offered, whatever the builder draws at the moment (a compile may show its model and
 *   hide the cells).
 * - `"compare"`: the builder is drawn to compare with what this mode shows. A pick is in Properties
 *   while its cells are drawn.
 * - `"away"`: the procedural model stays open behind the mode, put away. A pick is not this mode's
 *   selection: Properties leaves it out.
 *
 * A mode a UI gives none: `"compare"`. Core's own UI (no modes): always `"edit"`.
 */
export type BuilderPresence = "edit" | "compare" | "away";

/** One loaded model, as a mode's scene scope sees it. */
export interface SceneModel {
  /** The loaded source's name (its file name, or its key for a provider's model). */
  name: string;
}

/**
 * Which loaded models a mode draws (the rest are hidden while it is active, and come back after).
 * Core runs it, reversibly: what a model's visibility was before a scoped mode is what it goes back
 * to; a model the user shows or hides by hand while scoped is theirs until the mode changes.
 */
export interface SceneScopeSpec {
  /** True for a model this mode draws. Called on every pass; keep it cheap and pure. */
  draws: (model: SceneModel) => boolean;
}

/** What a mode does. Each field left out is core's default for a mode of a UI with modes. */
export interface ModeBehaviour {
  /** What the cell builder is here (`BuilderPresence`). Default `"compare"`. */
  builder?: BuilderPresence;
  /**
   * `"own"`: core sets the FEA field colouring aside while the mode is active and gives it back on
   * leaving (the mode paints its own, or none). `"results"`: the field colouring is shown.
   * Default: the mode's `ownsSceneColor`, else `"results"`.
   */
  sceneColor?: "own" | "results";
  /** Draw the FEA model deformed by its result. Default: the mode's `showsDeformation`. */
  deformation?: boolean;
  /** Which loaded models the mode draws. Default: all of them, untouched. */
  scene?: SceneScopeSpec;
  /** What the Simulations panel lists while the mode is active. Default: everything. */
  simulations?: ModeSimulations;
}

/**
 * What the Simulations panel lists in a mode (`ModeBehaviour.simulations`). The providers whose
 * plugin UI declares the mode (`SimulationProviderUi.mode`) are listed first; none is hidden for
 * declaring no mode, or another.
 */
export interface ModeSimulations {
  /** Only the providers of this domain (`"robotics"`, ...), and their runs. */
  domain?: string;
  /** What the panel says when no provider declares this mode (the others are still listed). */
  empty?: string;
}

/**
 * How a UI switches modes. Each field left out keeps core's default.
 */
export interface ModeSwitching {
  /** Open in the mode used last (kept per UI). Default true. */
  remember?: boolean;
  /** Honour `?mode=<id>` in the address. Default true. */
  urlParam?: boolean;
  /** The mode to fall back to when the active one stops being offered. Default: the first offered. */
  fallback?: ModeId;
  /** Ids this UI used before, and the mode each is now: an old link or remembered mode lands there. */
  aliases?: Readonly<Record<string, ModeId>>;
}
