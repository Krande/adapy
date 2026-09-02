import { create } from "zustand";

import {
  ApiError,
  viewerApi,
  type ProceduralBlueprintOption,
  type ProceduralCellTypeOption,
  type DetailingEngineSummary,
  type ProceduralDoc,
  type ProceduralDesignRulesetOption,
  type ProceduralEngineSummary,
  type ProceduralOpeningTypeOption,
  type ProceduralRelocationResult,
  type ProceduralSystemTypeOption,
  type ProceduralTypeOption,
} from "@/services/viewerApi";
import { Vector3 } from "three";

import {
  useConversionStore,
  type ConversionJob,
} from "@/state/conversionStore";
import { useModelState } from "@/state/modelState";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { useStatsStore } from "@/state/statsStore";
import { resolveSelectedBlueprint } from "@/utils/cellbuilder/blueprints";
import {
  resolveDetailingOptions,
  toDetailingOptionsPayload,
  type DetailingOptions,
} from "@/utils/cellbuilder/detailingOptions";
import {
  type CellGroup,
  groupAfterRemoval,
  groupToStructureName,
  normalizeGroups,
  resolveCellGroup,
  structureNameToGroup,
} from "@/utils/cellbuilder/groups";
import { pushSnapshot, redoStep, undoStep } from "@/utils/cellbuilder/history";
import {
  PORT_OVERRIDES_KEY,
  readPortOverrides,
  withPortOverride,
} from "@/utils/cellbuilder/ports";
import { postPreviewReady } from "@/utils/cellbuilder/proceduralChannel";
import {
  bandBounds,
  insertStation,
  memberToBands,
  removeStation,
  retypeStation,
  seedLoftMember,
  seedLoftMemberOnPlane,
  setExcludeFace,
  setStationParam,
  translateMember,
  type LoftBand,
  type LoftMemberDoc,
} from "@/utils/cellbuilder/loft";
import {
  applyFaceOffset,
  BOX_FACE_SIDES,
  cycleFaceIndex,
  edgeIndexInFace,
  extrudeBox,
  faceCenter,
  faceEdges,
  farFaceAfterExtrude,
  placeInCell,
  quantizeVec,
  withAxisLength,
  type CellBox,
  type CellSide,
  type CellSurface,
  type EdgeHit,
  type Vec3,
} from "@/utils/cellbuilder/snap";

// Procedural compile is a worker task (NATS queue, polled via convertStatus),
// so it reports through the same global toast panel (ConversionProgress) that
// conversion/FEA use — a spinner+progress row that resolves to success (auto-
// hides) or a dismissible error card. Keyed by the model so a re-compile updates
// the same toast in place; the key doubles as the human-readable label.
function proceduralToastKey(name: string): string {
  return `Procedural: ${name}`;
}

function setProceduralToast(name: string, patch: Partial<ConversionJob>): void {
  const key = proceduralToastKey(name);
  const conv = useConversionStore.getState();
  const prev = conv.jobs[key];
  conv.setJob(key, {
    sourceKey: key,
    jobId: prev?.jobId ?? "",
    derivedKey: prev?.derivedKey ?? "",
    status: "queued",
    progress: 0,
    stage: "",
    error: null,
    startedAt: prev?.startedAt ?? Date.now(),
    ...patch,
  });
}

// One box in the cellbuilder: either a space cell or an equipment unit.
// A `loft` cell is a swept band (one bay of a loft member) — it still carries
// origin/size (the band's bounding box) so box-oriented plumbing (hide,
// selection, the cell list) stays safe, but it is drawn from its two profile
// rings (see `loft`), not as a box. Its SHAPE is edited via the loft-station
// actions (Phase 3a: setLoftStationParam / insert / remove / moveLoftMember),
// which mutate the raw loftMembers and regenerate the band cells.
export interface BuilderCell extends CellBox {
  id: string;
  name: string;
  kind: "cell" | "equipment" | "opening" | "loft";
  /** Present only on `loft` cells: the two placed profile rings for this band
   * plus its member/bay/station metadata (derived from the raw loftMembers). */
  loft?: LoftBand;
  /** Archetype name (pump/tank/...) for equipment cells; from the
   * worker-advertised list. */
  equipmentType?: string;
  /** Subtype for `opening` cells (door / window / generic opening) — a
   * negative-volume box that cuts the wall/floor it overlaps; the subtype drives
   * which reinforcement the compiler frames around the hole (door: jambs +
   * lintel + threshold; window & opening: jambs + head + sill). */
  subtype?: "door" | "window" | "opening";
  /** Per-axis rotation in degrees (X, Y, Z), pivoting on the footprint centre —
   * equipment only. Undefined/all-zero means axis-aligned. Round-trips as the
   * entity's ROT_X/ROT_Y/ROT_Z; the compiler spins the body + ports to match. */
  rotation?: [number, number, number];
  /** Group this cell belongs to (a group is one structure compiled with its own
   * blueprint). Serializes as the space's `STRUCTURE_NAME`; undefined/blank means
   * ungrouped. Meaningful only for grouping-capable engines; the
   * built-in engine ignores it. Cells only. */
  group?: string;
  /** Extra pydantic entity fields (TopoSpace/TopoEquipment) beyond the
   * geometry: SE0..SE5 face exclusions, FLIP_FLOOR, SPACE_LOC, masses, ...
   * Round-tripped verbatim into the committed doc; the selection panel
   * exposes the curated editable subset. */
  params: Record<string, unknown>;
}

export type CellBuilderMode =
  | "idle"
  | "add-cell"
  | "add-equipment"
  | "add-opening"
  | "drag-face";

/** Active direct-manipulation gizmo for the selected cell. Rotate is an
 * equipment-only gizmo (spaces stay axis-aligned). */
export type GizmoMode = "none" | "translate" | "resize" | "rotate";

/** Outcome of a user-triggered equipment resync, for the summary popup:
 * per-slug lists of what happened plus a human-readable change log per slug. */
export interface ResyncSummary {
  created: string[];
  updated: string[];
  unchanged: string[];
  skipped: string[];
  changes: Record<string, string[]>;
}

/** The three model representations the user toggles between: the editable
 * topology cell model, the compiled simulation result, and the higher-fidelity
 * detail result (trimmed deck edges + modelled I-girder joints). */
export type RepresentationMode = "topology" | "simulation" | "detail";

export type SystemType = "piping" | "duct" | "cable" | "electrical";

/** One system endpoint: either an equipment port (equipment + port) OR a site
 * terminal — a model-boundary input/output (site name + world position + IN/OUT
 * direction) that closes a run which would otherwise dangle. ``directionVector``
 * is the terminal's outward orientation (the nozzle normal the run leaves along);
 * it defaults to +Z when omitted. */
export interface SystemConnection {
  equipment?: string;
  port?: string;
  site?: string;
  position?: [number, number, number];
  direction?: "IN" | "OUT";
  directionVector?: [number, number, number];
}

/** A logical service run between equipment ports. Rendered by the compiler as
 * a routed pipe/cable (see ada.topo_model.compile). */
export interface BuilderSystem {
  id: string;
  name: string;
  type: SystemType;
  medium?: string;
  connections: SystemConnection[];
}

/** Undoable model state. cells/systems maps are treated as immutable (every
 * mutating action spreads rather than mutates in place), so a snapshot is just
 * the current references — cheap to keep. */
export interface ModelSnapshot {
  cells: Record<string, BuilderCell>;
  /** The raw loft members ride in the snapshot too, so a loft shape edit
   * (Phase 3a) undoes/redoes atomically with its regenerated band cells. */
  loftMembers: LoftMemberDoc[];
  systems: Record<string, BuilderSystem>;
  blueprintOptions: Record<string, unknown>;
  equipmentCad: boolean;
  designRules: string;
  /** Cell groups (name + per-group blueprint). Undoable alongside cells so a
   * group add/rename/delete and its cell reassignments restore atomically. */
  groups: CellGroup[];
}

const HISTORY_LIMIT = 100;

// Re-exported so callers can keep importing the preview-compile gate from the
// store facade; the implementation lives in a store-free util for testability.
export { needsPreviewCompile } from "@/utils/cellbuilder/compileGate";

/** Current pick: a whole cell, one of its 6 faces (BoxGeometry materialIndex),
 * or a face border edge (full descriptor, so its endpoints re-derive from the
 * live box through resizes). */
export interface BuilderSelection {
  kind: "cell" | "face" | "edge";
  cellId: string;
  faceIndex?: number;
  edge?: EdgeHit;
}

/** What a plain click picks: nothing, the whole cell, the face under the
 * cursor, or the nearest border edge of that face. Selection is explicit —
 * the mode fully decides what a click resolves to (no implicit hover pick). */
export type SelectMode = "none" | "cell" | "face" | "edge";

export interface CompileJobState {
  jobId: string | null;
  derivedKey: string;
  status: "queued" | "running" | "done" | "error" | "cached";
  error?: string | null;
}

let _seq = 0;
const nextId = () => `cb_${++_seq}`;

function currentScopePart(): string {
  const scope = useScopeStore.getState().current;
  return scope ? scopeUrlPart(scope) : "user:me";
}

// Geometry/system keys consumed by the builder itself; everything else an
// entity dump carries lands in BuilderCell.params and round-trips verbatim.
const SPACE_OWN_KEYS = new Set(["NAME", "X", "Y", "Z", "DX", "DY", "DZ", "STRUCTURE_NAME"]);
const EQUIPMENT_OWN_KEYS = new Set([
  "NAME",
  "DESCRIPTION",
  "X",
  "Y",
  "Z",
  "LX",
  "LY",
  "LZ",
  "GLOBAL_COORDS",
  "ROT_X",
  "ROT_Y",
  "ROT_Z",
]);
const OPENING_OWN_KEYS = new Set([
  "NAME",
  "SUBTYPE",
  "USE_GLOBAL_COORDS",
  "X",
  "Y",
  "Z",
  "DX",
  "DY",
  "DZ",
]);

function extractParams(
  entity: Record<string, unknown>,
  ownKeys: Set<string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(entity)) {
    if (ownKeys.has(k) || v === null || v === undefined) continue;
    out[k] = v;
  }
  return out;
}

interface CellBuilderState {
  /** The procedural model open in the builder; null hides the whole tool
   * (top-row button included). */
  active: { modelId: string; name: string; revision: number } | null;
  cells: Record<string, BuilderCell>;
  /** Raw authored loft members — the editable source of truth for the `loft`
   * band cells (Phase 3a). Station/placement edits mutate this array and
   * regenerate the affected member's bands; toDoc re-emits it so a recompile
   * rebuilds the edited geometry. Per-face selection / openings on loft faces
   * are still deferred (design risk #1: no loft-native face id yet — Phase 3b). */
  loftMembers: LoftMemberDoc[];
  /** Logical service runs (rendered as routed pipes/cables by the compiler). */
  systems: Record<string, BuilderSystem>;
  mode: CellBuilderMode;
  selection: BuilderSelection | null;
  /** All currently-selected cells (the multi-select set, for copy-names / hide
   * multiple). Kept in sync with `selection`: a single pick is `[cellId]`; with
   * `cellAddMode` on, clicks toggle membership. `selection` remains the primary
   * (last-clicked) cell that drives the detail editors. */
  selectedCellIds: string[];
  /** When on, clicking a cell adds/removes it from `selectedCellIds` instead of
   * replacing the selection — the cell analogue of the regular "Add mode". */
  cellAddMode: boolean;
  selectMode: SelectMode;
  /** Live one-line status of the controller's keyboard tool (extrude / loft
   * numeric entry, etc.) surfaced in the Build tab, or null when idle. Set from
   * the controller since that state (the typed buffer) lives there. */
  toolHint: string | null;
  /** Which direct-manipulation gizmo is active for the selected cell: none, a
   * translate widget, or the face-handle resize gizmo. Reset to "none" whenever
   * the selected cell changes. */
  gizmoMode: GizmoMode;
  /** Blender-style axis constraint for the active translate/rotate gizmo: 0=X,
   * 1=Y, 2=Z, or null for unconstrained. Set by the X/Y/Z keys or the gizmo
   * HUD; restricts the visible/usable gizmo handle and scopes numeric entry.
   * Cleared whenever the gizmo mode or selection changes. */
  gizmoAxisLock: 0 | 1 | 2 | null;
  /** Vertex magnetism for the translate gizmo — snap the dragged cell's nearest
   * corner onto a neighbouring cell's corner within snapThreshold. On by
   * default. When an axis lock is active the snap is constrained to that axis
   * only (Blender behaviour), so a locked move still aligns to a face/vertex
   * along the lock without hopping off the axis. */
  gizmoVertexSnap: boolean;
  /** When translating a space cell, carry the equipment sitting inside it along
   * with the cell (rigid move). On by default. */
  moveEquipWithCell: boolean;
  /** Allow dragging a cell face in the scene to resize it. Off by default —
   * resizing goes through the explicit resize gizmo so plain navigation never
   * accidentally reshapes a cell. */
  faceDragResize: boolean;
  /** Cell context menu (long-press on touch / right-click on desktop): screen
   * position + the cell it was opened on. */
  contextMenu: { x: number; y: number; cellId: string } | null;
  /** "Insert equipment into/onto a cell" popover: screen position + the
   * equipment being re-seated (equipmentId), or null equipmentId to create a
   * new equipment. Opened from the + Equipment menu (new) or an equipment's
   * context menu (re-seat). */
  insertMenu: { x: number; y: number; equipmentId: string | null } | null;
  /** Right-click-a-port menu (choose Move / Rotate for that equipment port):
   * screen position + which port on which equipment it was opened on. */
  portMenu: { x: number; y: number; cellId: string; portName: string } | null;
  /** The equipment port currently being edited with a direct-manipulation
   * gizmo (translate = move the nozzle position, rotate = spin the outward
   * direction about the port anchor), or null. Independent of the cell gizmo
   * (`gizmoMode`); starting one clears the other. */
  portGizmo: {
    cellId: string;
    portName: string;
    mode: "translate" | "rotate";
  } | null;
  /** Equipment cell ids currently rendered as their type's CAD model in the
   * main viewer ("Show as CAD" per-object toggle). The 3D controller lazily
   * loads each type's preview GLB, seats it at the cell placement, and hides the
   * placeholder box for those cells; an empty list = every equipment shows its
   * box. Reset when the model unloads. A plain array so the controller's
   * reference-equality subscription fires on toggle. */
  cadPreviewCells: string[];
  gridStep: number;
  snapThreshold: number;
  dirty: boolean;
  autoCompile: boolean;
  committing: boolean;
  conflict: string | null;
  /** Equipment types for the add-equipment dropdown: code archetypes ∪ the
   * per-scope DB catalog, each tagged with its origin. */
  equipmentTypes: ProceduralTypeOption[];
  selectedEquipmentType: string | null; // a slug
  /** Space-cell types for the + Cell picker: built-in blueprints ∪ engine-
   * advertised, each with a default size + metadata. */
  cellTypes: ProceduralCellTypeOption[];
  selectedCellType: string | null; // a slug
  /** Opening types for the + Opening picker: built-in door/window ∪ engine-
   * advertised, each with a subtype + default size. */
  openingTypes: ProceduralOpeningTypeOption[];
  selectedOpeningType: string | null; // a slug
  /** System types for the systems inspector: code kinds ∪ DB templates. */
  systemTypes: ProceduralSystemTypeOption[];
  compileJob: CompileJobState | null;
  /** Engine messages captured during the most recent compile/preview (logging +
   * stdout), fetched once the job reaches done/cached/error. null = no log yet
   * (nothing compiled this session); "" = compiled but the engine emitted nothing. */
  compileLog: string | null;
  // The compile RUN the shown log belongs to (the job id), and whether that run
  // is the one just triggered. A cache hit shows the log of the run that BUILT
  // the artifact — still a real run, but not this one, so the panel says so
  // instead of passing off an old failure as the current build's.
  compileLogRunId: string | null;
  compileLogIsCurrentRun: boolean;
  /** Source name of the compiled SIMULATION result currently loaded in the scene. */
  resultSourceName: string | null;
  /** Source name of the compiled DETAIL result currently loaded in the scene. */
  detailSourceName: string | null;
  /** Which of the three model representations is shown: the topology cell model,
   * the simulation result, or the higher-fidelity detail result. Drives the
   * cell-overlay vs simulation-GLB vs detail-GLB visibility. */
  repMode: RepresentationMode;
  /** Superimpose the editable topology cell model UNDERNEATH the active result
   * (simulation/detail) instead of replacing it — so a compiled result renders
   * on top of the cells it came from. A view modifier on top of repMode: it only
   * has an effect while a result representation is active (topology is the base
   * layer). See setSuperimpose. */
  superimpose: boolean;
  /** Draw the compiled result BESIDE the editable topology (offset on +X) rather
   * than on top of it — one scene, one camera, only the result group moves. The
   * topology stays interactive at the origin, so you edit on the left and watch
   * the result update on the right. See setSideBySide. */
  sideBySide: boolean;
  /** Toggle the builder box meshes (hide to focus on the compiled structure). */
  cellsVisible: boolean;
  /** Individually hidden cells — ephemeral view state (not persisted, not
   * undoable), the per-cell analogue of the regular model's "Hide selected".
   * A hidden cell's box is invisible AND non-pickable, so clicks fall through
   * to whatever geometry (e.g. the compiled result) sits underneath. */
  hiddenCellIds: string[];
  /** Toggle the port/nozzle overlay: each placed equipment's input/output
   * positions + direction vectors drawn as coloured arrows (colours match the
   * catalog editor). Off by default. */
  portsOverlayVisible: boolean;
  /** Blueprint compile options round-tripped as doc.blueprint (whitelisted
   * server-side), e.g. {reinforce_internal_walls: true}. */
  blueprintOptions: Record<string, unknown>;
  /** When true, catalog equipment with a linked CAD asset render as the real
   * CAD geometry (spliced at compile) instead of a box. Round-trips as
   * doc.equipment_cad. */
  equipmentCad: boolean;
  /** Named design ruleset slug (routing/penetration rules); round-trips as
   * doc.design_rules and is resolved to callables by the compiler. */
  designRules: string;
  /** Available design rulesets for the ruleset dropdown (code ∪ worker). */
  designRulesets: ProceduralDesignRulesetOption[];
  /** Selected structural blueprint slug the compiler dispatches on; round-trips
   * as doc.blueprint_name. Null until the (engine-scoped) list is fetched. */
  selectedBlueprint: string | null;
  /** Available structural blueprints for the Blueprint dropdown, scoped to the
   * selected engine (built-in ∪ engine-advertised). Refetched on engine change. */
  blueprints: ProceduralBlueprintOption[];
  /** Selected procedural engine slug (compile-time only, not part of the model
   * document). "adapy-default" = the built-in compile. */
  selectedEngine: string;
  /** Available procedural engines for the engine dropdown (built-ins ∪ DB). */
  engines: ProceduralEngineSummary[];
  /** Selected detailing engine slug (fabrication-detail stage; COMPILE-time only,
   * not part of the document). "none" (default) = structural-only. */
  selectedDetailing: string;
  /** Available detailing engines for the Detailing dropdown (built-ins ∪ worker). */
  detailingEngines: DetailingEngineSummary[];
  /** Cell groups (name + per-group blueprint). A group is one structure the
   * (grouping-capable) engine compiles with its own blueprint; cells reference a
   * group by name via BuilderCell.group. Empty = single model-level blueprint
   * (backward compatible). Only meaningful for engines advertising
   * `supports_grouping`. */
  groups: CellGroup[];
  /** Undo/redo history over the model state (cells/systems/blueprintOptions). */
  past: ModelSnapshot[];
  future: ModelSnapshot[];
  /** >0 while a coalesced edit (e.g. a face drag) is in progress — mutations
   * within don't push their own history entry. */
  txDepth: number;
  panelVisible: boolean;

  open: (
    modelId: string,
    name: string,
    revision: number,
    doc: ProceduralDoc,
  ) => void;
  close: () => void;
  setMode: (mode: CellBuilderMode) => void;
  setSelection: (sel: BuilderSelection | null) => void;
  /** Toggle a cell in the multi-select set (used when cellAddMode is on); the
   * toggled cell becomes the primary selection. */
  toggleCellSelection: (cellId: string) => void;
  /** Flip the cell add-mode (sticky, like the regular additive select). */
  toggleCellAddMode: () => void;
  setSelectMode: (m: SelectMode) => void;
  setToolHint: (hint: string | null) => void;
  setGizmoMode: (mode: GizmoMode) => void;
  /** Lock/unlock the active gizmo to one axis (null clears the constraint). */
  setGizmoAxisLock: (axis: 0 | 1 | 2 | null) => void;
  /** Toggle vertex magnetism for the translate gizmo. */
  setGizmoVertexSnap: (on: boolean) => void;
  /** Toggle carrying contained equipment when a cell is translated. */
  setMoveEquipWithCell: (on: boolean) => void;
  /** Move a cell by `delta` metres along `axis` (origin-quantised, undoable) —
   * the Blender-style "G, X, 2, Enter" numeric nudge. */
  translateCellAlongAxis: (id: string, axis: 0 | 1 | 2, delta: number) => void;
  setFaceDragResize: (v: boolean) => void;
  openContextMenu: (x: number, y: number, cellId: string) => void;
  closeContextMenu: () => void;
  openInsertMenu: (x: number, y: number, equipmentId: string | null) => void;
  closeInsertMenu: () => void;
  /** Open the port context menu (right-click a port arrow) — Move / Rotate. */
  openPortMenu: (
    x: number,
    y: number,
    cellId: string,
    portName: string,
  ) => void;
  closePortMenu: () => void;
  /** Begin editing an equipment port with the translate/rotate gizmo. */
  startPortGizmo: (
    cellId: string,
    portName: string,
    mode: "translate" | "rotate",
  ) => void;
  /** Flip the active port gizmo between move and rotate (no-op if none). */
  setPortGizmoMode: (mode: "translate" | "rotate") => void;
  /** Stop editing the port (detach the gizmo). */
  stopPortGizmo: () => void;
  /** Toggle whether an equipment cell renders as its type's CAD model (vs the
   * placeholder box) in the main viewer. No-op for non-equipment cells. */
  toggleCadPreview: (cellId: string) => void;
  /** Persist a per-instance port edit (position and/or outward direction, in
   * the equipment's LOCAL frame) as an override on the equipment cell — it
   * round-trips through the doc so it survives a recompile. Undoable. */
  updateEquipmentPort: (
    cellId: string,
    portName: string,
    patch: {
      position?: [number, number, number];
      direction_vector?: [number, number, number];
    },
  ) => void;
  /** Seat equipment onto/into a cell: create a new equipment (equipmentId
   * null) or re-position an existing one on the chosen surface/side, centred
   * on the cell footprint. */
  insertEquipmentIntoCell: (opts: {
    equipmentId: string | null;
    cellId: string;
    surface: CellSurface;
    side: CellSide;
  }) => void;
  setPanelVisible: (v: boolean) => void;
  /** The system to spotlight in the Systems inspector — set by a "Procedural
   * model" panel link so clicking a routed run's system opens + highlights it.
   * Cleared when the inspector consumes it. */
  focusedSystemName: string | null;
  /** Open the cellbuilder panel and spotlight the named system in the Systems
   * inspector (link target from the selected-object procedural panel). */
  focusSystem: (name: string) => void;
  /** Open the cellbuilder panel and select the named equipment cell so its
   * info shows (link target from the selected-object procedural panel). */
  focusEquipment: (name: string) => void;
  setCellsVisible: (v: boolean) => void;
  /** Recompute the viewer model translation from the current cells so the model
   * sits centred in the scene. Fixes a skewed placement left over after deleting
   * a far-off cell/equipment that had stretched the original bounding box. */
  recenterModel: () => void;
  /** Hide the given cells (per-cell "Hide selected"). */
  hideCells: (ids: string[]) => void;
  /** Clear all per-cell hides. */
  unhideAllCells: () => void;
  setPortsOverlayVisible: (v: boolean) => void;
  setGridStep: (v: number) => void;
  setSnapThreshold: (v: number) => void;
  setAutoCompile: (v: boolean) => void;
  setEquipmentCad: (v: boolean) => void;
  setDesignRules: (slug: string) => void;
  setSelectedEngine: (slug: string) => void;
  fetchEngines: () => Promise<void>;
  /** Select the detailing engine (compile-time; "none" = structural-only). */
  setSelectedDetailing: (slug: string) => void;
  fetchDetailingEngines: () => Promise<void>;
  /** Per-joint-type detailing options (toggle + field values), reconciled against
   * the SELECTED detailing engine's advertised joint_types. Empty for "none". */
  detailingOptions: DetailingOptions;
  /** Optional per-joint-type DETECTED counts from the last detailing compile
   * ({jointSlug: n}); null until a compile reports them. Drives the "Detected
   * joints" readout in the Detailing tab when present. */
  detailingJointCounts: Record<string, number> | null;
  /** Toggle a joint family on/off in the Detailing tab. */
  setDetailingJointEnabled: (jointSlug: string, enabled: boolean) => void;
  /** Set one generated field on a joint family in the Detailing tab. */
  setDetailingField: (
    jointSlug: string,
    fieldName: string,
    value: number | boolean | string,
  ) => void;
  /** The per-joint option map the compile call ships as `detailing_options`
   * (`{slug: {enabled, <field>}}`); null when no detailing engine is selected. */
  detailingOptionsPayload: () => ReturnType<typeof toDetailingOptionsPayload>;
  /** Select the structural blueprint (doc.blueprint_name); marks the model dirty. */
  setSelectedBlueprint: (slug: string) => void;
  /** Set one advertised blueprint parameter into doc.blueprint (e.g. a section
   * profile like `girder_sec`); marks the model dirty so a recompile picks it up. */
  setBlueprintOption: (name: string, value: unknown) => void;
  /** Fetch the blueprints the SELECTED engine offers and reconcile the current
   * selection (keep it if still offered, else the engine's default). */
  fetchBlueprints: () => Promise<void>;
  /** Add a new cell group (auto-named; blueprint defaults to the engine's
   * default / current selection). Undoable; marks the model dirty. */
  addGroup: () => void;
  /** Rename a group (from -> to); reassigns every cell pointing at it. A blank or
   * duplicate target is ignored. Undoable; marks the model dirty. */
  renameGroup: (from: string, to: string) => void;
  /** Delete a group; unassigns its cells (back to ungrouped). Undoable; dirty. */
  removeGroup: (name: string) => void;
  /** Set a group's structural blueprint. Undoable; marks the model dirty. */
  setGroupBlueprint: (name: string, blueprint: string) => void;
  /** Assign a cell to a group (or null to clear). Undoable; marks the model dirty. */
  setCellGroup: (cellId: string, groupName: string | null) => void;
  setSelectedEquipmentType: (t: string | null) => void;
  setSelectedCellType: (t: string | null) => void;
  setSelectedOpeningType: (t: string | null) => void;
  /** Step the active cell type through the advertised catalog (keyboard T). */
  cycleCellType: (dir: 1 | -1) => void;
  /** Step the active equipment type through the advertised catalog (keyboard
   * equipment-insert mode). */
  cycleEquipmentType: (dir: 1 | -1) => void;
  /** Keyboard equipment insert: create equipment of the selected type at
   * `local` (X,Y) in the host cell's LOCAL frame, seated on the cell floor.
   * Selects the new equipment. One undo step. */
  insertEquipmentAtLocal: (
    cellId: string,
    local: [number, number],
  ) => void;
  /** Keyboard extrude: grow a NEW cell adjacent to a selected face — same
   * cross-section, `depth` metres deep along the face axis (negative flips the
   * direction). The new cell's far face is auto-selected so a repeated extrude
   * chains outward. One undo step. */
  extendCellFromFace: (
    cellId: string,
    faceIndex: number,
    depth: number,
  ) => void;
  /** Cycle the selection's granularity cell -> face -> edge (keyboard Tab),
   * re-deriving the current pick at the new level on the same cell. */
  cycleSelectMode: (dir: 1 | -1) => void;
  /** Cycle the active element (keyboard F/D): faces of a box cell, edges of a
   * face in edge mode, or bays/stations of a loft member. */
  cycleSelectionElement: (dir: 1 | -1) => void;
  /** Select the next/previous cell by name order (keyboard N/P). */
  selectAdjacentCell: (dir: 1 | -1) => void;
  /** Type-derived sizing: resize every placed equipment of a given type to the
   * catalog bbox (kept centred on its footprint). Called when the equipment
   * type's bbox is edited in the admin panel. */
  resizeEquipmentOfType: (slug: string, bbox: [number, number, number]) => void;
  /** Mark a cell as a fully-enclosed room (plated walls + decks) or not, by
   * toggling its name in blueprintOptions.enclosed_cells. */
  setCellEnclosed: (cellName: string, enclosed: boolean) => void;
  addCell: (
    kind: "cell" | "equipment" | "opening",
    origin: Vec3,
    size: Vec3,
  ) => void;
  /** Insert a door/window opening straddling a selected cell FACE, sized to a
   * sensible default (door: 0.9x2.1 at the floor; window: 1.2x1.0 at a 1.0 m
   * sill; a floor/roof face gets a 0.9x0.9 hatch). The new opening becomes the
   * selection. No-op unless the face belongs to a space cell. */
  insertOpeningOnFace: (
    cellId: string,
    faceIndex: number,
    subtype: "door" | "window" | "opening",
  ) => void;
  updateCell: (id: string, patch: Partial<BuilderCell>) => void;
  /** Move a space cell to `newOrigin`, carrying `equipIds` (its contained
   * equipment) by the same delta — equipment moves rigidly with its cell. */
  moveCellAndEquipment: (
    id: string,
    newOrigin: [number, number, number],
    equipIds: string[],
  ) => void;
  /** Set an equipment cell's absolute per-axis rotation (degrees). No-op for
   * non-equipment cells. Undoable — the gizmo wraps a drag in a transaction. */
  setCellRotation: (id: string, rotation: [number, number, number]) => void;
  /** Desktop shortcut: move the selected equipment (or opening) up (+1) / down
   * (-1) one cell floor level, preserving its height offset within the floor and
   * re-homing SPACE_NAME to the space cell it lands in. No-op for space cells or
   * when there's no floor in that direction. Undoable. */
  bumpSelectedFloor: (delta: 1 | -1) => void;
  /** Rename a cell/equipment; for equipment, rewrites matching system
   * connections so no run is orphaned. No-op on an empty/duplicate name. */
  renameCell: (id: string, name: string) => void;
  setCellParam: (id: string, key: string, value: unknown) => void;
  /** Extend (positive) / contract (negative) a face outward by `length`. */
  applyFaceExtension: (id: string, faceIndex: number, length: number) => void;
  /** Set the box length along `axis` (origin fixed). */
  setEdgeLength: (id: string, axis: 0 | 1 | 2, length: number) => void;
  removeCell: (id: string) => void;
  /** Edit one station's numeric param (Z/X/Y/WIDTH/HEIGHT/RADIUS). Rebuilds the
   * member's band cells, marks dirty, undoable. WIDTH/HEIGHT/RADIUS clamp >= 0. */
  setLoftStationParam: (
    memberName: string,
    stationIndex: number,
    key: string,
    value: number,
  ) => void;
  /** Insert a station after `afterIndex`, splitting that bay in two (or
   * extending past the last station). Bay count grows by one; undoable. */
  insertLoftStation: (memberName: string, afterIndex: number) => void;
  /** Remove the station at `stationIndex`, merging its adjacent bays. Refused
   * below 2 stations (the backend minimum); undoable. */
  removeLoftStation: (memberName: string, stationIndex: number) => void;
  /** Delete a whole loft member and all its bay cells (Del on a single-bay loft,
   * or an explicit remove). One undo step. */
  removeLoftMember: (memberName: string) => void;
  /** Translate a whole loft member by `delta` (world metres) via its PLACEMENT
   * translation column — moves every bay; undoable. */
  moveLoftMember: (memberName: string, delta: Vec3) => void;
  /** Rename a loft member (updates its bay cell names). Rejects a name already
   * taken by another loft member; undoable. */
  renameLoftMember: (memberName: string, name: string) => void;
  /** Add/remove a MEMBER-RELATIVE loft face id (e.g. `"bay0:edge2"`,
   * `"bay0:cap_lo"` — see `bandFaceIds`) in the member's EXCLUDE_FACES (Phase
   * 3b). `excluded=true` drops the face (its plate is omitted on recompile),
   * `false` restores it. Regenerates the member's band cells so the proxy dims
   * the removed panels; round-trips through toDoc; undoable. */
  setLoftFaceExcluded: (
    memberName: string,
    memberRelativeFaceId: string,
    excluded: boolean,
  ) => void;
  /** Replace a loft member's user-defined METADATA map (empty clears it).
   * Geometry-neutral — round-trips verbatim through the member; undoable. */
  setLoftMemberMetadata: (
    memberName: string,
    metadata: Record<string, unknown>,
  ) => void;
  /** Keyboard "new loft" (L): append a fresh 2-station circle member seeded at
   * the model ground origin and select its first bay. One undo step. */
  /** Start a new loft member (L). With `base` (from a selected cell face), the
   * loft grows out of that face — a rectangle tube on the face plane, sized to
   * the face, extruded along its normal. Without it, a default circle at ground. */
  addLoftMember: (base?: {
    placement: number[][];
    width: number;
    height: number;
  }) => void;
  /** Keyboard extrude for lofts (E): add a station `spacing` metres above the
   * member's top station and select the new top bay. One undo step. */
  extendLoftStack: (memberName: string, spacing: number) => void;
  /** Keyboard station resize (S): set the station's primary section dimension —
   * RADIUS (circle) or WIDTH·HEIGHT together (rectangle). One undo step. */
  resizeLoftStation: (
    memberName: string,
    stationIndex: number,
    primary: number,
  ) => void;
  /** Keyboard station retype (T): flip a station's section rectangle<->circle,
   * seeding sensible dimensions. One undo step. */
  setLoftStationType: (
    memberName: string,
    stationIndex: number,
    type: "rectangle" | "circle",
  ) => void;
  addSystem: (
    type: SystemType,
    opts?: { name?: string; medium?: string | null },
  ) => void;
  updateSystem: (id: string, patch: Partial<BuilderSystem>) => void;
  removeSystem: (id: string) => void;
  addSystemConnection: (id: string, conn: SystemConnection) => void;
  removeSystemConnection: (id: string, index: number) => void;
  /** Systems whose connections reference the given equipment name. */
  systemsForEquipment: (equipmentName: string) => BuilderSystem[];
  toDoc: () => ProceduralDoc;
  loadFromDoc: (doc: ProceduralDoc) => void;
  /** Restore the previous / next model snapshot. */
  undo: () => void;
  redo: () => void;
  /** Coalesce a burst of mutations (e.g. a face drag) into one undo step. */
  beginTransaction: () => void;
  endTransaction: () => void;
  fetchEquipmentTypes: () => Promise<void>;
  fetchCellTypes: () => Promise<void>;
  fetchOpeningTypes: () => Promise<void>;
  fetchSystemTypes: () => Promise<void>;
  fetchDesignRulesets: () => Promise<void>;
  /** Persist a code-origin type into the scope's DB catalog, then refresh. */
  syncEquipmentTypeToDb: (slug: string) => Promise<void>;
  syncSystemTypeToDb: (slug: string) => Promise<void>;
  /** Upsert ALL code equipment archetypes into the catalog, updating existing
   * entries so code changes (new ports, corrected heights) reach placed
   * equipment. ``quiet`` suppresses the toast when nothing changed (auto-resync
   * on model open). Returns the per-slug outcome, or null on failure. */
  resyncEquipmentTypes: (opts?: { quiet?: boolean }) => Promise<{
    created: string[];
    updated: string[];
    unchanged: string[];
    skipped: string[];
    changes: Record<string, string[]>;
  } | null>;
  /** True while a (non-quiet) resync is in flight, so the button can disable. */
  resyncBusy: boolean;
  /** Result of the last user-triggered resync, shown as a summary popup listing
   * which equipment changed and how. Null when dismissed / never run. */
  resyncSummary: ResyncSummary | null;
  dismissResyncSummary: () => void;
  commit: () => Promise<boolean>;
  /** Compile the active model. ``force`` recompiles even if the revision's GLB
   * is already cached — used when the compiler engine changed but the document
   * (the cache key) did not, so a plain Compile would return the stale blob. */
  compile: (force?: boolean, lod?: "sim" | "detail") => Promise<void>;
  /** Build the current (uncommitted) document as an ephemeral preview — no
   * commit, no revision bump; the interactive visualise-then-commit loop. */
  compilePreview: (force?: boolean, lod?: "sim" | "detail") => Promise<void>;
  /** Preview the LOD(s) selected by buildSim/buildDetail (the Compile button and
   * the ⇧↵ shortcut). Builds each so switching views is instant. */
  compilePreviewSelected: (force?: boolean) => Promise<void>;
  /** Which level(s) of detail a Compile produces: simulation, detail, or both. */
  buildSim: boolean;
  buildDetail: boolean;
  setBuildSim: (on: boolean) => void;
  setBuildDetail: (on: boolean) => void;
  /** Compile the CURRENT (uncommitted) doc entirely in the browser via the
   * built-in adapy engine (Pyodide/WASM), loading the result straight into the
   * scene — no server round-trip, no commit. Catalog/CAD equipment falls back to
   * archetypes/boxes (the browser has no DB). */
  compileInBrowser: () => Promise<void>;
  viewResult: (
    derivedKey: string,
    lod?: "sim" | "detail",
    /** Explicit scene source name.
     *
     * Without it the name is derived from whichever model is ACTIVE, which is
     * fine while the cellbuilder is the only caller but makes "is this model's
     * result in the scene?" unanswerable for any other one: the same model
     * loads under a different name depending on what was active at the time.
     * The storage panel passes a name derived from the model itself, so a row
     * can show whether its result is loaded — and for the active model the two
     * rules agree, because that name IS active.name. */
    sourceName?: string,
  ) => Promise<void>;
  hideResult: () => void;
  hideDetail: () => void;
  /** Switch the active model representation (topology / simulation / detail),
   * coordinating the cell overlay and the two result GLB sources. Compiles/loads
   * the target result lazily the first time its view is opened. */
  setRepMode: (mode: RepresentationMode) => Promise<void>;
  setSuperimpose: (on: boolean) => Promise<void>;
  /** Toggle the side-by-side result view (result offset beside the topology). */
  setSideBySide: (on: boolean) => void;
  /** The last relocation proposals (or null). Populated by proposeRelocations;
   * applied only when the user clicks Apply. */
  relocations: ProceduralRelocationResult | null;
  relocationBusy: boolean;
  /** Analyse the model and propose the minimum equipment moves that clear its
   * cramped/unroutable runs. Commits first (the worker reads the committed doc).
   * Never applies them — sets `relocations` for the panel to show. */
  proposeRelocations: () => Promise<void>;
  /** Apply the current proposals: move each named equipment to its proposed
   * position (converting origin → box corner) and mark dirty. Clears
   * `relocations`. The user then recompiles. */
  applyRelocations: () => void;

  // ── Excel round-trip ──────────────────────────────────────────────
  /** True while an Excel export or import job is in flight (disables the
   * Export/Import buttons). */
  xlsxBusy: boolean;
  /** A staged import awaiting an engine choice: set when an uploaded workbook had
   * no `_ADA_META` sheet so the engine couldn't be auto-detected. Non-null shows
   * the engine-picker prompt in the panel. */
  importPrompt: { sourceKey: string; name: string } | null;
  /** Export the active model to its (selected) engine's Excel workbook and
   * trigger a browser download. Commits first when there are unsaved edits so the
   * workbook matches what's on screen. */
  exportToExcel: () => Promise<void>;
  /** Export + download the committed model as a CAD/analysis file: "ifc" (the
   * DETAIL model, clash cuts as IfcRelVoidsElement voids) or "gxml" (the
   * SIMULATION model as a Genie concept XML). Commits first when dirty. IFC
   * honours `exportIfcCad` (splice real catalog CAD equipment). */
  exportModel: (format: "ifc" | "gxml") => Promise<void>;
  /** IFC export: splice real catalog CAD geometry for equipment (default on).
   * Off = placeholder boxes. gxml ignores this (Genie equipment concept type). */
  exportIfcCad: boolean;
  setExportIfcCad: (v: boolean) => void;
  /** Begin importing a workbook: upload it, auto-detect the owning engine from its
   * `_ADA_META` sheet, and import immediately when detected — otherwise set
   * `importPrompt` so the user picks an engine. */
  beginImportFromExcel: (file: File) => Promise<void>;
  /** Resolve an import that needed an engine choice (from the prompt). The
   * caller passes the prompt captured at render time: the menu that hosts the
   * engine picker dismisses (running `cancelImport`, which nulls `importPrompt`)
   * BEFORE the item's click handler fires, so reading `importPrompt` back from
   * the store here would race to null and silently drop the import. */
  confirmImportEngine: (
    engine: string,
    prompt?: { sourceKey: string; name: string },
  ) => Promise<void>;
  /** Dismiss the pending-import engine prompt without importing. */
  cancelImport: () => void;
}

// Global offset that seats a cell-associated equipment at its cell. Equipment
// X/Y/Z are LOCAL to their SPACE_NAME cell — the default — unless GLOBAL_COORDS
// is set, matching the compile worker (equipment_space_offset) and the
// simulation view. A ROOF-seated unit also picks up the cell height. Global or
// unresolved-cell equipment get no offset (X/Y/Z are already world coords). This
// is why an imported model's equipment used to render at the wrong spot: their
// local coords were placed as if global.
function equipmentSpaceOffset(
  e: Record<string, unknown>,
  spaceByName: Map<string, Record<string, unknown>>,
): [number, number, number] {
  if (e.GLOBAL_COORDS) return [0, 0, 0];
  const s = spaceByName.get(e.SPACE_NAME as string);
  if (!s) return [0, 0, 0];
  const oz =
    Number(s.Z ?? 0) + (e.SPACE_LOC === "ROOF" ? Number(s.DZ ?? 0) : 0);
  return [Number(s.X ?? 0), Number(s.Y ?? 0), oz];
}

/** The doc's cell groups (name + per-group blueprint), normalized (blank/dup
 * names dropped). Empty for an ungrouped doc. */
function groupsFromDoc(doc: ProceduralDoc): CellGroup[] {
  return normalizeGroups(
    (doc.groups ?? []).map((g) => ({
      name: String(g.name ?? ""),
      blueprint: String(g.blueprint ?? ""),
    })),
  );
}

function cellsFromDoc(doc: ProceduralDoc): Record<string, BuilderCell> {
  const out: Record<string, BuilderCell> = {};
  const spaceByName = new Map<string, Record<string, unknown>>();
  for (const s of doc.spaces ?? []) {
    const nm = s.NAME as string | undefined;
    if (nm) spaceByName.set(nm, s);
  }
  // Reconcile each cell's group against the doc's group list: a STRUCTURE_NAME
  // naming no defined group is dropped to ungrouped (keeps the model consistent).
  const groups = groupsFromDoc(doc);
  for (const s of doc.spaces ?? []) {
    const id = nextId();
    out[id] = {
      id,
      name: String(s.NAME ?? id),
      kind: "cell",
      origin: [Number(s.X ?? 0), Number(s.Y ?? 0), Number(s.Z ?? 0)],
      size: [Number(s.DX ?? 1), Number(s.DY ?? 1), Number(s.DZ ?? 1)],
      group: resolveCellGroup(structureNameToGroup(s), groups),
      params: extractParams(s, SPACE_OWN_KEYS),
    };
  }
  for (const e of doc.equipments ?? []) {
    const id = nextId();
    const [ox, oy, oz] = equipmentSpaceOffset(e, spaceByName);
    out[id] = {
      id,
      name: String(e.NAME ?? id),
      kind: "equipment",
      equipmentType:
        typeof e.DESCRIPTION === "string" && e.DESCRIPTION
          ? e.DESCRIPTION
          : undefined,
      origin: [
        Number(e.X ?? 0) + ox,
        Number(e.Y ?? 0) + oy,
        Number(e.Z ?? 0) + oz,
      ],
      size: [Number(e.LX ?? 1), Number(e.LY ?? 1), Number(e.LZ ?? 1)],
      rotation: [
        Number(e.ROT_X ?? 0),
        Number(e.ROT_Y ?? 0),
        Number(e.ROT_Z ?? 0),
      ],
      params: extractParams(e, EQUIPMENT_OWN_KEYS),
    };
  }
  // Openings are UI-placed as global-coord negative-volume boxes (X/Y/Z/DX/DY/DZ).
  // A locally-placed opening imported from elsewhere without global coords is
  // skipped in the builder (still round-trips through params on commit only if it
  // has coords) — the cellbuilder authors global ones.
  for (const o of (doc as { openings?: Record<string, unknown>[] }).openings ??
    []) {
    if (o.X == null || o.DX == null) continue;
    const id = nextId();
    out[id] = {
      id,
      name: String(o.NAME ?? id),
      kind: "opening",
      subtype:
        o.SUBTYPE === "window" ? "window" : o.SUBTYPE === "opening" ? "opening" : "door",
      origin: [Number(o.X ?? 0), Number(o.Y ?? 0), Number(o.Z ?? 0)],
      size: [Number(o.DX ?? 1), Number(o.DY ?? 1), Number(o.DZ ?? 1)],
      params: extractParams(o, OPENING_OWN_KEYS),
    };
  }
  // Loft members (Phase 2b, read-only): each member -> N-1 swept-band cells,
  // drawn from their two profile rings. INCLUDE=false members are skipped
  // (memberToBands returns []). The raw loft_members are retained separately on
  // the store so toDoc re-emits them verbatim — this slice never edits loft
  // geometry. A loft-only model (no spaces) loads + displays.
  for (const m of loftMembersFromDoc(doc)) {
    for (const band of memberToBands(m)) {
      const id = nextId();
      const { origin, size } = bandBounds(band);
      out[id] = {
        id,
        name: band.cellName,
        kind: "loft",
        origin,
        size,
        loft: band,
        params: {},
      };
    }
  }
  return out;
}

/** The raw authored loft members carried on a doc (empty when absent). */
function loftMembersFromDoc(doc: ProceduralDoc): LoftMemberDoc[] {
  const raw = (doc as { loft_members?: unknown }).loft_members;
  return Array.isArray(raw) ? (raw as LoftMemberDoc[]) : [];
}

/** Rebuild ONE member's band `loft` cells (Phase 3a edit) into a new cells map,
 * leaving all other cells (and other members' bands) untouched. Existing bay
 * cell ids are preserved by matching `${NAME}_bay{i}` name — so a param edit or
 * a whole-member move (band count unchanged) keeps every id stable, and the
 * live selection + translate gizmo survive the rebuild. An insert/remove shifts
 * the bay indices, so the shifted bays get fresh ids (the caller re-maps the
 * selection). */
function regenLoftMemberCells(
  cells: Record<string, BuilderCell>,
  member: LoftMemberDoc,
): Record<string, BuilderCell> {
  const idByName = new Map<string, string>();
  const out: Record<string, BuilderCell> = {};
  for (const [id, c] of Object.entries(cells)) {
    if (c.kind === "loft" && c.loft?.member === member.NAME) {
      idByName.set(c.name, id);
    } else {
      out[id] = c;
    }
  }
  for (const band of memberToBands(member)) {
    const id = idByName.get(band.cellName) ?? nextId();
    const { origin, size } = bandBounds(band);
    out[id] = {
      id,
      name: band.cellName,
      kind: "loft",
      origin,
      size,
      loft: band,
      params: {},
    };
  }
  return out;
}

/** Replace the member named `name` in a loft-members array (identity when
 * absent). */
function replaceLoftMember(
  members: LoftMemberDoc[],
  name: string,
  next: LoftMemberDoc,
): LoftMemberDoc[] {
  return members.map((m) => (m.NAME === name ? next : m));
}

/** After a structural loft edit (insert/remove) drops/renames the selected bay
 * cell, re-home the selection onto the member's bay at `fallbackBay` (clamped),
 * or clear it. Param edits/moves preserve ids so this is skipped for them. */
function remapLoftSelection(
  s: CellBuilderState,
  cells: Record<string, BuilderCell>,
  memberName: string,
  fallbackBay: number,
): Partial<CellBuilderState> {
  const sel = s.selection;
  if (sel && cells[sel.cellId]) return {}; // still valid — nothing to do
  const bands = Object.values(cells).filter(
    (c) => c.kind === "loft" && c.loft?.member === memberName,
  );
  const bay = Math.min(Math.max(fallbackBay, 0), bands.length - 1);
  const target = bands.find((c) => c.loft?.bay === bay);
  if (target)
    return {
      selection: { kind: "cell", cellId: target.id },
      selectedCellIds: [target.id],
    };
  return { selection: null, selectedCellIds: [], gizmoMode: "none" };
}

function systemsFromDoc(doc: ProceduralDoc): Record<string, BuilderSystem> {
  const out: Record<string, BuilderSystem> = {};
  for (const s of doc.systems ?? []) {
    const id = nextId();
    const conns = Array.isArray(s.CONNECTIONS)
      ? (s.CONNECTIONS as Record<string, unknown>[])
      : [];
    out[id] = {
      id,
      name: String(s.NAME ?? id),
      type: (typeof s.TYPE === "string" ? s.TYPE : "piping") as SystemType,
      medium: typeof s.MEDIUM === "string" ? s.MEDIUM : undefined,
      connections: conns.map((c) =>
        c.SITE
          ? {
              site: String(c.SITE),
              position: Array.isArray(c.POSITION)
                ? ([
                    Number(c.POSITION[0]),
                    Number(c.POSITION[1]),
                    Number(c.POSITION[2]),
                  ] as [number, number, number])
                : ([0, 0, 0] as [number, number, number]),
              direction: c.DIRECTION === "OUT" ? "OUT" : "IN",
              directionVector: Array.isArray(c.DIRECTION_VECTOR)
                ? ([
                    Number(c.DIRECTION_VECTOR[0]),
                    Number(c.DIRECTION_VECTOR[1]),
                    Number(c.DIRECTION_VECTOR[2]),
                  ] as [number, number, number])
                : undefined,
            }
          : {
              equipment: String(c.EQUIPMENT ?? ""),
              port: String(c.PORT ?? ""),
            },
      ),
    };
  }
  return out;
}

function containingCellName(
  cells: Record<string, BuilderCell>,
  eq: BuilderCell,
): string {
  const cx = eq.origin[0] + eq.size[0] / 2;
  const cy = eq.origin[1] + eq.size[1] / 2;
  for (const c of Object.values(cells)) {
    if (c.kind !== "cell") continue;
    const inX = cx >= c.origin[0] && cx <= c.origin[0] + c.size[0];
    const inY = cy >= c.origin[1] && cy <= c.origin[1] + c.size[1];
    if (inX && inY) return c.name;
  }
  const first = Object.values(cells).find((c) => c.kind === "cell");
  return first ? first.name : "NoSpace";
}

/** The topology's X-width from its cells (0 when empty). */
function modelXWidth(cells: Record<string, BuilderCell>): number {
  let minX = Infinity;
  let maxX = -Infinity;
  for (const c of Object.values(cells)) {
    minX = Math.min(minX, c.origin[0]);
    maxX = Math.max(maxX, c.origin[0] + c.size[0]);
  }
  return maxX > minX ? maxX - minX : 0;
}

/** The topology's far +X EDGE in model space (0 when empty).
 *
 * This, not the width, is what the side-by-side offset needs: the result has to
 * start past where the topology ENDS. A cell model authored from the origin
 * outward has edge == width, which is why a width-based formula appeared to
 * work — but the two diverge the moment cells do not start at 0, and the result
 * then overlaps by exactly that difference. */
function modelMaxX(cells: Record<string, BuilderCell>): number {
  let maxX = -Infinity;
  for (const c of Object.values(cells)) {
    maxX = Math.max(maxX, c.origin[0] + c.size[0]);
  }
  return Number.isFinite(maxX) && maxX > 0 ? maxX : 0;
}

function snapshot(s: CellBuilderState): ModelSnapshot {
  return {
    cells: s.cells,
    loftMembers: s.loftMembers,
    systems: s.systems,
    blueprintOptions: s.blueprintOptions,
    equipmentCad: s.equipmentCad,
    designRules: s.designRules,
    groups: s.groups,
  };
}

/** After an undo/redo restores a snapshot, drop a selection pointing at a cell
 * that no longer exists. */
function pruneSelection(
  sel: BuilderSelection | null,
  cells: Record<string, BuilderCell>,
): BuilderSelection | null {
  return sel && cells[sel.cellId] ? sel : null;
}

export const useCellBuilderStore = create<CellBuilderState>((set, get) => {
  // Wrap a model-mutating updater so it pushes the pre-change snapshot onto
  // the undo stack (and clears the redo stack) — unless a transaction owns
  // the snapshot for this burst of edits.
  const withHistory = (
    updater: (s: CellBuilderState) => Partial<CellBuilderState>,
  ) =>
    set((s) => {
      const partial = updater(s);
      // No-op updater (e.g. target cell gone) -> no state change, no history.
      if (!partial || Object.keys(partial).length === 0) return {};
      if (s.txDepth > 0) return partial;
      return { ...partial, ...pushSnapshot(s, snapshot(s), HISTORY_LIMIT) };
    });

  // Drive a procedural build (a committed compile OR an ephemeral preview) to
  // completion: announce the toast, enqueue, then poll job status and auto-show
  // the result when ready. Shared by compile() and compilePreview() so the two
  // paths behave identically apart from what they enqueue.
  const startCompileJob = async (
    label: string,
    lod: "sim" | "detail",
    enqueue: () => Promise<{
      job_id: string | null;
      derived_key: string;
      cached?: boolean;
    }>,
  ): Promise<void> => {
    setProceduralToast(label, {
      status: "queued",
      stage: "queued",
      progress: 0,
      startedAt: Date.now(),
    });
    // Clear any prior log while this build runs; it's refetched on completion.
    set({ compileLog: null, compileLogRunId: null, compileLogIsCurrentRun: false });
    // Fetch the engine-compile log for a finished (or failed) build and stash it
    // so the panel's "Compile log" section can show the engine's messages. Best
    // effort — a missing log resolves to "" and never blocks the result.
    const fetchCompileLog = async (derivedKey: string, runId?: string | null) => {
      const active = get().active;
      if (!active || (!derivedKey && !runId)) return;
      try {
        const res = await viewerApi.proceduralCompileLog(
          currentScopePart(),
          active.modelId,
          derivedKey,
          runId,
        );
        set({
          compileLog: res.text,
          compileLogRunId: res.runId || null,
          compileLogIsCurrentRun: Boolean(runId) && res.runId === runId,
        });
      } catch {
        // Leave compileLog as-is; the log is a diagnostic, not load-bearing.
      }
    };
    // Fetch the quantity take-off computed alongside the build (Stats panel).
    // Best effort — a model without a sidecar degrades to "not available".
    const fetchStats = (derivedKey: string) => {
      const active = get().active;
      if (!active || !derivedKey) return;
      void useStatsStore
        .getState()
        .fetchModelStats(currentScopePart(), active.modelId, derivedKey)
        .then(() => {
          // The take-off carries the per-type joint counts (from the detailing
          // stage). Surface them as the Detailing tab's "N detected" badges; a
          // model with no joints clears the badges.
          const joints = useStatsStore.getState().stats?.joints;
          const counts = joints?.by_type?.length
            ? Object.fromEntries(joints.by_type.map((t) => [t.slug, t.count]))
            : null;
          set({ detailingJointCounts: counts });
        });
    };
    // Announce a ready server build to any follower tab (BroadcastChannel), so a
    // second window opened with ?pfollow=<modelId> can load and show it live.
    const broadcast = (derivedKey: string) => {
      const active = get().active;
      if (!active || !derivedKey) return;
      postPreviewReady({
        modelId: active.modelId,
        scope: currentScopePart(),
        derivedKey,
        lod,
        name: active.name,
      });
    };
    // Show the freshly-built result — WITHOUT moving the camera (viewResult loads
    // with autoFit off) and WITHOUT superimposing it on the topology. Topology and
    // result are separate layers: the result renders only when it's the thing to
    // show — either side-by-side is on (result sits beside the topology) or a
    // matching result view is active. In plain Topology view it stays hidden, so
    // the topology view shows only topology. Only the LOD the current view wants
    // renders, so building "both" never double-draws.
    const autoShow = () => {
      const cur = get().compileJob;
      if (!cur || !cur.derivedKey) return;
      const rm = lod === "detail" ? "detail" : "simulation";
      if (get().sideBySide) {
        // Result sits BESIDE the topology (left = topology, right = result). Only
        // render the LOD the current view wants, so building "both" doesn't stack
        // two results on the right; leave repMode alone (topology stays on left).
        // Compare in the LOD vocabulary ("sim"/"detail"), NOT repMode's
        // ("simulation"/"detail") — otherwise a sim build ("sim") never matched
        // "simulation" and the result silently never refreshed beside topology.
        const wantLod = get().repMode === "detail" ? "detail" : "sim";
        if (lod === wantLod) void get().viewResult(cur.derivedKey, lod);
        return;
      }
      // Not side-by-side: this result IS the view. Switch to it from Topology (or
      // refresh it if already there) — result-only (no superimpose), no camera
      // move. Don't yank the user out of a DIFFERENT result view they're reading.
      if (get().repMode === "topology" || get().repMode === rm) {
        if (get().repMode !== rm) {
          set({ repMode: rm });
          get().setCellsVisible(get().superimpose);
          if (rm === "simulation") get().hideDetail();
          else get().hideResult();
        }
        void get().viewResult(cur.derivedKey, lod);
      }
    };
    try {
      const res = await enqueue();
      if (res.cached) {
        set({
          compileJob: { jobId: null, derivedKey: res.derived_key, status: "cached" },
        });
        setProceduralToast(label, {
          status: "done",
          progress: 1,
          stage: "cached",
          derivedKey: res.derived_key,
        });
        autoShow();
        broadcast(res.derived_key);
        void fetchCompileLog(res.derived_key, null);
        fetchStats(res.derived_key);
        return;
      }
      set({
        compileJob: { jobId: res.job_id, derivedKey: res.derived_key, status: "queued" },
      });
      setProceduralToast(label, {
        status: "queued",
        stage: "queued",
        jobId: res.job_id ?? "",
        derivedKey: res.derived_key,
      });
      const jobId = res.job_id!;
      const poll = async () => {
        const cur = get().compileJob;
        if (!cur || cur.jobId !== jobId) return; // superseded
        try {
          const st = await viewerApi.convertStatus(jobId);
          if (st.status === "done") {
            set({ compileJob: { ...cur, status: "done" } });
            setProceduralToast(label, {
              status: "done",
              progress: 1,
              stage: st.stage || "ready",
              derivedKey: st.derived_key || cur.derivedKey || "",
            });
            autoShow();
            broadcast(st.derived_key || cur.derivedKey || "");
            void fetchCompileLog(st.derived_key || cur.derivedKey || "", jobId);
            fetchStats(st.derived_key || cur.derivedKey || "");
            return;
          }
          if (st.status === "error") {
            set({
              compileJob: { ...cur, status: "error", error: st.error ?? "compile failed" },
            });
            setProceduralToast(label, {
              status: "error",
              stage: st.stage || "",
              error: st.error ?? "compile failed",
            });
            // A failed compile still persists its log (errors are inspectable).
            void fetchCompileLog(cur.derivedKey || "", jobId);
            return;
          }
          set({ compileJob: { ...cur, status: "running" } });
          setProceduralToast(label, {
            status: "running",
            progress: st.progress ?? 0,
            stage: st.stage || "",
            jobId,
          });
          setTimeout(poll, 1500);
        } catch (e) {
          const error = e instanceof Error ? e.message : String(e);
          set({ compileJob: { ...cur, status: "error", error } });
          setProceduralToast(label, { status: "error", error });
        }
      };
      setTimeout(poll, 1500);
    } catch (e) {
      const error = e instanceof Error ? e.message : String(e);
      set({ compileJob: { jobId: null, derivedKey: "", status: "error", error } });
      setProceduralToast(label, { status: "error", error });
    }
  };

  // Enqueue an import job for a staged workbook (source_key) with a resolved
  // engine, poll it, and open the freshly-created model on success. Shared by the
  // auto-detected path and the "picked an engine from the prompt" path.
  const IMPORT_LABEL = "Import from Excel";
  const runImport = async (
    sourceKey: string,
    engine: string,
    name: string,
  ): Promise<void> => {
    const scope = currentScopePart();
    setProceduralToast(IMPORT_LABEL, {
      status: "running",
      progress: 0.3,
      stage: "importing…",
    });
    const openImported = async (derivedKey: string) => {
      try {
        const result = await viewerApi.fetchProceduralImportResult(scope, derivedKey);
        const detail = await viewerApi.getProceduralModel(scope, result.model_id);
        get().open(detail.id, detail.name, detail.revision, detail.doc);
        setProceduralToast(IMPORT_LABEL, {
          status: "done",
          progress: 1,
          stage: "imported",
          derivedKey,
        });
      } catch (e) {
        setProceduralToast(IMPORT_LABEL, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      } finally {
        set({ xlsxBusy: false });
      }
    };
    try {
      const res = await viewerApi.importProceduralModelXlsx(scope, {
        source_key: sourceKey,
        engine,
        name,
      });
      const jobId = res.job_id;
      const poll = async () => {
        try {
          const st = await viewerApi.convertStatus(jobId);
          if (st.status === "done") {
            await openImported(st.derived_key || res.derived_key);
            return;
          }
          if (st.status === "error") {
            set({ xlsxBusy: false });
            setProceduralToast(IMPORT_LABEL, {
              status: "error",
              stage: st.stage || "",
              error: st.error ?? "import failed",
            });
            return;
          }
          setProceduralToast(IMPORT_LABEL, {
            status: "running",
            progress: st.progress ?? 0.4,
            stage: st.stage || "importing…",
            jobId,
          });
          setTimeout(poll, 1500);
        } catch (e) {
          set({ xlsxBusy: false });
          setProceduralToast(IMPORT_LABEL, {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          });
        }
      };
      setTimeout(poll, 1200);
    } catch (e) {
      set({ xlsxBusy: false });
      setProceduralToast(IMPORT_LABEL, {
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      });
    }
  };

  return {
    active: null,
    cells: {},
    loftMembers: [],
    systems: {},
    past: [],
    future: [],
    txDepth: 0,
    mode: "idle",
    selection: null,
    // Clicking a cell/equipment selects it (the Selected Object Info panel then
    // shows its procedural detail). Selection is NOT editing: with faceDragResize
    // off, a tap selects and a drag orbits — moving/resizing geometry only
    // happens through the explicit translate/resize gizmos. A click that misses
    // every cell (or lands on a per-cell-hidden, click-through box) falls through
    // to normal geometry selection, so the same panel updates for the compiled
    // result too.
    selectedCellIds: [],
    cellAddMode: false,
    selectMode: "cell",
    toolHint: null,
    gizmoMode: "none",
    gizmoAxisLock: null,
    gizmoVertexSnap: true,
    moveEquipWithCell: true,
    faceDragResize: false,
    contextMenu: null,
    insertMenu: null,
    portMenu: null,
    portGizmo: null,
    cadPreviewCells: [],
    gridStep: 0.1,
    snapThreshold: 0.25,
    dirty: false,
    autoCompile: true,
    committing: false,
    conflict: null,
    equipmentTypes: [],
    selectedEquipmentType: null,
    cellTypes: [],
    selectedCellType: null,
    openingTypes: [],
    selectedOpeningType: null,
    systemTypes: [],
    compileJob: null,
    compileLog: null,
    compileLogRunId: null,
    compileLogIsCurrentRun: false,
    resultSourceName: null,
    detailSourceName: null,
    repMode: "topology",
    superimpose: false,
    sideBySide: false,
    buildSim: true,
    buildDetail: false,
    relocations: null,
    relocationBusy: false,
    xlsxBusy: false,
    exportIfcCad: true,
    importPrompt: null,
    resyncBusy: false,
    resyncSummary: null,
    cellsVisible: true,
    hiddenCellIds: [],
    portsOverlayVisible: false,
    blueprintOptions: {},
    equipmentCad: false,
    designRules: "standard",
    designRulesets: [],
    selectedBlueprint: null,
    blueprints: [],
    selectedEngine: "adapy-default",
    engines: [],
    selectedDetailing: "none",
    detailingEngines: [],
    detailingOptions: {},
    detailingJointCounts: null,
    groups: [],
    panelVisible: false,
    focusedSystemName: null,

    open: (modelId, name, revision, doc) => {
      // A freshly loaded model starts a new editing session — history resets.
      set({
        active: { modelId, name, revision },
        cells: cellsFromDoc(doc),
        loftMembers: loftMembersFromDoc(doc),
        systems: systemsFromDoc(doc),
        groups: groupsFromDoc(doc),
        blueprintOptions: doc.blueprint ?? {},
        equipmentCad: Boolean(doc.equipment_cad),
        designRules: doc.design_rules ?? "standard",
        // The structural blueprint the model was authored with (a legacy doc
        // without one defaults to steel_stru); reconciled against the engine's
        // offered list by fetchBlueprints below.
        selectedBlueprint: doc.blueprint_name ?? "steel_stru",
        // Reflect the engine this model was built for in the dropdown (a
        // capability-engine example opens on that engine, not the adapy-default default).
        selectedEngine: doc.engine || "adapy-default",
        // Detailing selection now rides on the document (a template can default it
        // to adapy-default); absent = "none" (structural-only). Its per-joint
        // options are reconciled from the advertised specs by fetchDetailingEngines
        // below.
        selectedDetailing: doc.detailing ?? "none",
        detailingOptions: {},
        past: [],
        future: [],
        txDepth: 0,
        mode: "idle",
        selection: null,
        selectedCellIds: [],
        gizmoMode: "none",
        contextMenu: null,
        insertMenu: null,
        portMenu: null,
        portGizmo: null,
        cadPreviewCells: [],
        dirty: false,
        conflict: null,
        compileJob: null,
        compileLog: null,
        compileLogRunId: null,
        compileLogIsCurrentRun: false,
        panelVisible: true,
        hiddenCellIds: [],
        // Reset the VIEW state to a clean topology view. Without this, a session
        // that left off in a result view (repMode "simulation" sets cellsVisible
        // = superimpose||sideBySide, i.e. false) taints the next open: the reopened
        // model shows repMode "topology" but with its cells still hidden (empty
        // view) until a repMode toggle re-runs setCellsVisible(true).
        repMode: "topology",
        cellsVisible: true,
        superimpose: false,
        sideBySide: false,
      });
      // Center the new model from its own cells — resets any translation left
      // over from a previously-open model, so fit-all (Shift+A) and empty-space
      // cell placement use THIS model's bounds, not the last one's.
      get().recenterModel();
      void get().fetchEquipmentTypes();
      void get().fetchCellTypes();
      void get().fetchOpeningTypes();
      void get().fetchSystemTypes();
      // Auto-update the catalog from code on open: if a code archetype changed
      // (new port, corrected height), refresh the scope's synced entries so a
      // recompile uses them. Quiet unless something actually changed.
      void get()
        .resyncEquipmentTypes({ quiet: true })
        .then((res) => {
          if (res && res.updated.length + res.created.length > 0)
            void get().fetchEquipmentTypes();
        });
      void get().fetchDesignRulesets();
      void get().fetchEngines();
      void get().fetchDetailingEngines();
      // Blueprints are engine-scoped; fetch for this model's engine and reconcile
      // the selection loaded above against what the engine actually offers.
      void get().fetchBlueprints();
    },
    close: () => {
      get().hideResult();
      get().hideDetail();
      set({
        active: null,
        cells: {},
        loftMembers: [],
        systems: {},
        past: [],
        future: [],
        txDepth: 0,
        mode: "idle",
        selection: null,
        selectedCellIds: [],
        gizmoMode: "none",
        contextMenu: null,
        insertMenu: null,
        portMenu: null,
        portGizmo: null,
        cadPreviewCells: [],
        dirty: false,
        panelVisible: false,
        compileJob: null,
        compileLog: null,
        compileLogRunId: null,
        compileLogIsCurrentRun: false,
        hiddenCellIds: [],
        // Clean view state on close so it can't taint the next open (see open()).
        repMode: "topology",
        cellsVisible: true,
        superimpose: false,
        sideBySide: false,
      });
    },
    setMode: (mode) => set({ mode }),
    // Switching to a different cell (or clearing) drops the active gizmo so it
    // never lingers on a cell you're no longer editing. Selecting a cell does
    // NOT force the Selected Object Info panel open — the user opens it when
    // they want it; the cell/system details render there once it's visible.
    setSelection: (selection) => {
      set((s) => ({
        selection,
        // A single pick resets the multi-select set to just this cell.
        selectedCellIds: selection ? [selection.cellId] : [],
        gizmoMode:
          selection && s.selection && selection.cellId === s.selection.cellId
            ? s.gizmoMode
            : "none",
        // A new pick drops any axis constraint from the previous gizmo session.
        gizmoAxisLock: null,
      }));
    },
    toggleCellSelection: (cellId) =>
      set((s) => {
        if (!s.cells[cellId]) return {};
        const has = s.selectedCellIds.includes(cellId);
        const next = has
          ? s.selectedCellIds.filter((id) => id !== cellId)
          : [...s.selectedCellIds, cellId];
        // Primary selection follows the click: the added cell, or (when
        // removing) the last one still selected, else nothing.
        const primaryId = has ? next[next.length - 1] : cellId;
        return {
          selectedCellIds: next,
          selection: primaryId ? { kind: "cell", cellId: primaryId } : null,
          gizmoMode: "none",
        };
      }),
    toggleCellAddMode: () => set((s) => ({ cellAddMode: !s.cellAddMode })),
    setSelectMode: (selectMode) => set({ selectMode }),
    setToolHint: (toolHint) => set({ toolHint }),
    // Switching gizmo (or turning it off) drops any axis constraint — and any
    // active port-edit gizmo, so the cell and port gizmos never fight.
    setGizmoMode: (gizmoMode) =>
      set({ gizmoMode, gizmoAxisLock: null, portGizmo: null }),
    setGizmoAxisLock: (gizmoAxisLock) => set({ gizmoAxisLock }),
    setGizmoVertexSnap: (gizmoVertexSnap) => set({ gizmoVertexSnap }),
    setMoveEquipWithCell: (moveEquipWithCell) => set({ moveEquipWithCell }),
    translateCellAlongAxis: (id, axis, delta) =>
      withHistory((s) => {
        const cur = s.cells[id];
        if (!cur || !delta) return {};
        const origin: Vec3 = [...cur.origin];
        origin[axis] = origin[axis] + delta;
        return {
          cells: {
            ...s.cells,
            [id]: { ...cur, origin: quantizeVec(origin, s.gridStep) },
          },
          dirty: true,
        };
      }),
    setFaceDragResize: (faceDragResize) => set({ faceDragResize }),
    openContextMenu: (x, y, cellId) => set({ contextMenu: { x, y, cellId } }),
    closeContextMenu: () => set({ contextMenu: null }),
    openInsertMenu: (x, y, equipmentId) =>
      set({ insertMenu: { x, y, equipmentId }, contextMenu: null }),
    closeInsertMenu: () => set({ insertMenu: null }),
    openPortMenu: (x, y, cellId, portName) =>
      set({ portMenu: { x, y, cellId, portName }, contextMenu: null }),
    closePortMenu: () => set({ portMenu: null }),
    startPortGizmo: (cellId, portName, mode) =>
      // Starting a port edit clears the cell gizmo + the menu it came from.
      set({
        portGizmo: { cellId, portName, mode },
        portMenu: null,
        gizmoMode: "none",
        gizmoAxisLock: null,
      }),
    setPortGizmoMode: (mode) =>
      set((s) => (s.portGizmo ? { portGizmo: { ...s.portGizmo, mode } } : {})),
    stopPortGizmo: () => set({ portGizmo: null }),
    toggleCadPreview: (cellId) =>
      set((s) => {
        const cell = s.cells[cellId];
        if (!cell || cell.kind !== "equipment") return {};
        const on = s.cadPreviewCells.includes(cellId);
        return {
          cadPreviewCells: on
            ? s.cadPreviewCells.filter((id) => id !== cellId)
            : [...s.cadPreviewCells, cellId],
        };
      }),
    updateEquipmentPort: (cellId, portName, patch) =>
      withHistory((s) => {
        const cur = s.cells[cellId];
        if (!cur || cur.kind !== "equipment") return {};
        const overrides = readPortOverrides(cur.params);
        const next = withPortOverride(overrides, portName, patch);
        const params = { ...cur.params, [PORT_OVERRIDES_KEY]: next };
        return {
          cells: { ...s.cells, [cellId]: { ...cur, params } },
          dirty: true,
        };
      }),
    insertEquipmentIntoCell: ({ equipmentId, cellId, surface, side }) =>
      withHistory((s) => {
        const cell = s.cells[cellId];
        if (!cell || cell.kind !== "cell") return {};
        const step = s.gridStep || 0.1;
        // SPACE_LOC metadata records the seating surface (descriptive; the
        // compiled geometry follows the absolute X/Y/Z we author here).
        const spaceLoc = surface === "roof" ? "ROOF" : "FLOOR";
        if (equipmentId) {
          // Re-seat an existing equipment onto/into the chosen cell.
          const eq = s.cells[equipmentId];
          if (!eq || eq.kind !== "equipment") return {};
          const origin = placeInCell(cell, eq.size, surface, side, step);
          return {
            cells: {
              ...s.cells,
              [equipmentId]: {
                ...eq,
                origin,
                params: { ...eq.params, SPACE_LOC: spaceLoc },
              },
            },
            dirty: true,
            mode: "idle",
            selection: { kind: "cell", cellId: equipmentId },
            insertMenu: null,
          };
        }
        // Create a new equipment seated on the cell (mirrors addCell's naming).
        const id = nextId();
        const count =
          Object.values(s.cells).filter((c) => c.kind === "equipment").length +
          1;
        const eqType = s.selectedEquipmentType ?? undefined;
        const baseName = (eqType ?? "EQ").toUpperCase();
        const size: Vec3 = [1, 1, 1];
        const eqCell: BuilderCell = {
          id,
          name: `${baseName}_${String(count).padStart(2, "0")}`,
          kind: "equipment",
          equipmentType: eqType,
          origin: placeInCell(cell, size, surface, side, step),
          size,
          params: { SPACE_LOC: spaceLoc },
        };
        return {
          cells: { ...s.cells, [id]: eqCell },
          dirty: true,
          mode: "idle",
          selection: { kind: "cell", cellId: id },
          insertMenu: null,
        };
      }),
    setPanelVisible: (panelVisible) => set({ panelVisible }),
    focusSystem: (name) => set({ panelVisible: true, focusedSystemName: name }),
    focusEquipment: (name) => {
      const cell = Object.values(get().cells).find(
        (c) => c.kind === "equipment" && c.name === name,
      );
      if (!cell) {
        set({ panelVisible: true });
        return;
      }
      set({
        panelVisible: true,
        focusedSystemName: null,
        selection: { kind: "cell", cellId: cell.id },
        selectedCellIds: [cell.id],
      });
    },
    setCellsVisible: (cellsVisible) => set({ cellsVisible }),
    recenterModel: () => {
      const cells = Object.values(get().cells);
      if (!cells.length) {
        // Empty model — reset to the origin so it doesn't inherit a prior
        // model's offset (which would skew fit-all / cell placement).
        useModelState.getState().setTranslation(new Vector3(0, 0, 0));
        return;
      }
      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      for (const c of cells) {
        minX = Math.min(minX, c.origin[0]);
        maxX = Math.max(maxX, c.origin[0] + c.size[0]);
        minY = Math.min(minY, c.origin[1]);
        maxY = Math.max(maxY, c.origin[1] + c.size[1]);
        minZ = Math.min(minZ, c.origin[2]);
        maxZ = Math.max(maxZ, c.origin[2] + c.size[2]);
      }
      const t = new Vector3(-(minX + maxX) / 2, -(minY + maxY) / 2, -(minZ + maxZ) / 2);
      // Match the GLB loader's convention (setupModelLoader): centre X/Y, and put
      // the model's bottom ~5% of its height above the ground plane on the up axis.
      const ms = useModelState.getState();
      if (ms.zIsUp) t.z = -minZ + (maxZ - minZ) * 0.05;
      else t.y = -minY + (maxY - minY) * 0.05;
      // The controller subscribes to translation changes and re-syncs the cell
      // container; a fresh Vector3 ref makes the subscription fire.
      ms.setTranslation(t);
    },
    hideCells: (ids) =>
      set((s) => {
        const next = new Set(s.hiddenCellIds);
        // Only hide ids that are real cells. A hidden cell shouldn't stay
        // selected (its box is now click-through) — drop it from the
        // multi-select set and clear the primary selection when it's hidden.
        for (const id of ids) if (s.cells[id]) next.add(id);
        const selHidden = s.selection && next.has(s.selection.cellId);
        return {
          hiddenCellIds: [...next],
          selectedCellIds: s.selectedCellIds.filter((id) => !next.has(id)),
          ...(selHidden
            ? { selection: null, gizmoMode: "none" as GizmoMode }
            : {}),
        };
      }),
    unhideAllCells: () => set({ hiddenCellIds: [] }),
    setPortsOverlayVisible: (portsOverlayVisible) =>
      set({ portsOverlayVisible }),
    setGridStep: (gridStep) => set({ gridStep: Math.max(0, gridStep) }),
    setSnapThreshold: (snapThreshold) =>
      set({ snapThreshold: Math.max(0, snapThreshold) }),
    setAutoCompile: (autoCompile) => set({ autoCompile }),
    setEquipmentCad: (equipmentCad) =>
      withHistory(() => ({ equipmentCad, dirty: true })),
    setDesignRules: (designRules) =>
      withHistory(() => ({ designRules, dirty: true })),
    setSelectedEquipmentType: (selectedEquipmentType) =>
      set({ selectedEquipmentType }),
    setSelectedCellType: (selectedCellType) => set({ selectedCellType }),
    setSelectedOpeningType: (selectedOpeningType) =>
      set({ selectedOpeningType }),
    cycleCellType: (dir) =>
      set((s) => {
        if (!s.cellTypes.length) return {};
        const slugs = s.cellTypes.map((t) => t.slug);
        const i = slugs.indexOf(s.selectedCellType ?? "");
        const ni = (((i < 0 ? 0 : i) + dir) % slugs.length + slugs.length) %
          slugs.length;
        return { selectedCellType: slugs[ni] };
      }),
    cycleEquipmentType: (dir) =>
      set((s) => {
        if (!s.equipmentTypes.length) return {};
        const slugs = s.equipmentTypes.map((t) => t.slug);
        const i = slugs.indexOf(s.selectedEquipmentType ?? "");
        const ni = (((i < 0 ? 0 : i) + dir) % slugs.length + slugs.length) %
          slugs.length;
        return { selectedEquipmentType: slugs[ni] };
      }),
    insertEquipmentAtLocal: (cellId, local) =>
      withHistory((s) => {
        const cell = s.cells[cellId];
        if (!cell || cell.kind !== "cell") return {};
        const id = nextId();
        const count =
          Object.values(s.cells).filter((c) => c.kind === "equipment").length + 1;
        const eqType = s.selectedEquipmentType ?? undefined;
        const baseName = (eqType ?? "EQ").toUpperCase();
        const size: Vec3 = [1, 1, 1];
        // Cell-local (X,Y) -> world origin (the cells map stores world coords);
        // seat on the cell floor (origin z = cell floor).
        const origin = quantizeVec(
          [cell.origin[0] + local[0], cell.origin[1] + local[1], cell.origin[2]],
          s.gridStep,
        );
        const eqCell: BuilderCell = {
          id,
          name: `${baseName}_${String(count).padStart(2, "0")}`,
          kind: "equipment",
          equipmentType: eqType,
          origin,
          size,
          params: { SPACE_LOC: "FLOOR" },
        };
        return {
          cells: { ...s.cells, [id]: eqCell },
          dirty: true,
          mode: "idle",
          selection: { kind: "cell", cellId: id },
          selectedCellIds: [id],
        };
      }),
    extendCellFromFace: (cellId, faceIndex, depth) =>
      withHistory((s) => {
        const cur = s.cells[cellId];
        const side = BOX_FACE_SIDES[faceIndex];
        if (!cur || cur.kind !== "cell" || !side || !depth) return {};
        const box = extrudeBox(cur, faceIndex, depth);
        if (box.size[side.axis] <= 0) return {};
        const id = nextId();
        const count =
          Object.values(s.cells).filter((c) => c.kind === "cell").length + 1;
        // Inherit the active cell type's entity metadata, exactly like addCell.
        const cellType = s.cellTypes.find((t) => t.slug === s.selectedCellType);
        const cell: BuilderCell = {
          id,
          name: `CELL_${String(count).padStart(2, "0")}`,
          kind: "cell",
          origin: quantizeVec(box.origin, s.gridStep),
          size: quantizeVec(box.size, s.gridStep),
          params: cellType?.metadata ? { ...cellType.metadata } : {},
        };
        return {
          cells: { ...s.cells, [id]: cell },
          dirty: true,
          mode: "idle",
          // Auto-select the new cell's far face so a repeated E chains outward.
          selection: {
            kind: "face",
            cellId: id,
            faceIndex: farFaceAfterExtrude(faceIndex, depth),
          },
          selectedCellIds: [id],
        };
      }),
    cycleSelectMode: (dir) =>
      set((s) => {
        const sel = s.selection;
        if (!sel) return {};
        const order: SelectMode[] = ["cell", "face", "edge"];
        const i = order.indexOf(sel.kind);
        const mode = order[(((i < 0 ? 0 : i) + dir) % 3 + 3) % 3];
        const cellId = sel.cellId;
        const fi = sel.faceIndex ?? 0;
        const selection: BuilderSelection =
          mode === "cell"
            ? { kind: "cell", cellId }
            : mode === "face"
              ? { kind: "face", cellId, faceIndex: fi }
              : { kind: "edge", cellId, faceIndex: fi, edge: faceEdges(fi)[0] };
        return { selectMode: mode, selection, selectedCellIds: [cellId] };
      }),
    cycleSelectionElement: (dir) =>
      set((s) => {
        const sel = s.selection;
        if (!sel) return {};
        const cell = s.cells[sel.cellId];
        if (!cell) return {};
        // Loft band: cycle between the member's bays (its stations).
        if (cell.kind === "loft" && cell.loft) {
          const member = cell.loft.member;
          const bands = Object.values(s.cells)
            .filter((c) => c.kind === "loft" && c.loft?.member === member)
            .sort((a, b) => (a.loft!.bay ?? 0) - (b.loft!.bay ?? 0));
          if (!bands.length) return {};
          const i = bands.findIndex((c) => c.id === sel.cellId);
          const c = bands[(((i < 0 ? 0 : i) + dir) % bands.length + bands.length) %
            bands.length];
          return { selection: { kind: "cell", cellId: c.id }, selectedCellIds: [c.id] };
        }
        // Edge mode: cycle the current face's four border edges.
        if (sel.kind === "edge" && sel.faceIndex != null && sel.edge) {
          const edges = faceEdges(sel.faceIndex);
          const i = edgeIndexInFace(sel.faceIndex, sel.edge);
          const ni = (((i < 0 ? 0 : i) + dir) % edges.length + edges.length) %
            edges.length;
          return { selection: { ...sel, edge: edges[ni] } };
        }
        // Otherwise cycle box faces (promoting a whole-cell pick to a face).
        const start = sel.faceIndex ?? (dir > 0 ? -1 : 0);
        return {
          selection: {
            kind: "face",
            cellId: sel.cellId,
            faceIndex: cycleFaceIndex(start, dir),
          },
          selectedCellIds: [sel.cellId],
        };
      }),
    selectAdjacentCell: (dir) =>
      set((s) => {
        const list = Object.values(s.cells).sort((a, b) =>
          a.name.localeCompare(b.name),
        );
        if (!list.length) return {};
        const i = list.findIndex((c) => c.id === s.selection?.cellId);
        const from = i < 0 ? (dir > 0 ? -1 : 0) : i;
        const c = list[((from + dir) % list.length + list.length) % list.length];
        return {
          selection: { kind: "cell", cellId: c.id },
          selectedCellIds: [c.id],
          gizmoMode: "none",
        };
      }),

    setCellEnclosed: (cellName, enclosed) =>
      withHistory((s) => {
        const cur = Array.isArray(
          (s.blueprintOptions as { enclosed_cells?: unknown }).enclosed_cells,
        )
          ? (s.blueprintOptions as { enclosed_cells: string[] }).enclosed_cells
          : [];
        const names = new Set(cur);
        if (enclosed) names.add(cellName);
        else names.delete(cellName);
        return {
          blueprintOptions: {
            ...s.blueprintOptions,
            enclosed_cells: [...names],
          },
          dirty: true,
        };
      }),

    resizeEquipmentOfType: (slug, [lx, ly, lz]) =>
      set((s) => {
        // Plain (non-undoable) sync from the catalog edit — keep each unit
        // centred on its footprint (x/y) and seated at its base z.
        let changed = false;
        const cells = { ...s.cells };
        for (const [id, c] of Object.entries(s.cells)) {
          if (c.kind !== "equipment" || c.equipmentType !== slug) continue;
          const cx = c.origin[0] + c.size[0] / 2;
          const cy = c.origin[1] + c.size[1] / 2;
          cells[id] = {
            ...c,
            size: [lx, ly, lz],
            origin: [cx - lx / 2, cy - ly / 2, c.origin[2]],
          };
          changed = true;
        }
        return changed ? { cells, dirty: true } : {};
      }),

    addCell: (kind, origin, size) =>
      withHistory((s) => {
        const id = nextId();
        const count =
          Object.values(s.cells).filter((c) => c.kind === kind).length + 1;
        const eqType =
          kind === "equipment"
            ? (s.selectedEquipmentType ?? undefined)
            : undefined;
        // Cell/opening defaults (subtype + entity metadata) come from the
        // engine-advertised type the picker selected — not hardcoded here. The
        // door fallback only applies if the opening catalog is unreachable.
        const cellType =
          kind === "cell"
            ? s.cellTypes.find((t) => t.slug === s.selectedCellType)
            : undefined;
        const openingType =
          kind === "opening"
            ? s.openingTypes.find((t) => t.slug === s.selectedOpeningType)
            : undefined;
        const subtype =
          kind === "opening" ? (openingType?.subtype ?? "door") : undefined;
        const baseName =
          kind === "cell"
            ? "CELL"
            : kind === "opening"
              ? "OPENING"
              : (eqType ?? "EQ").toUpperCase();
        const cell: BuilderCell = {
          id,
          name: `${baseName}_${String(count).padStart(2, "0")}`,
          kind,
          equipmentType: eqType,
          subtype,
          origin: quantizeVec(origin, s.gridStep),
          size: quantizeVec(size, s.gridStep),
          // A cell type may carry extra TopoSpace entity fields (round-tripped
          // verbatim); openings/equipment start with none.
          params: cellType?.metadata ? { ...cellType.metadata } : {},
        };
        // A freshly placed cell becomes the selection, but we leave the
        // Selected Object Info panel's visibility untouched.
        return {
          cells: { ...s.cells, [id]: cell },
          dirty: true,
          mode: "idle",
          selection: { kind: "cell", cellId: id },
        };
      }),

    insertOpeningOnFace: (cellId, faceIndex, subtype) =>
      withHistory((s) => {
        const cell = s.cells[cellId];
        const side = BOX_FACE_SIDES[faceIndex];
        if (!cell || cell.kind !== "cell" || !side) return {};
        // Point on the selected face plane, then straddle it with a thin box so
        // the opening reliably overlaps the wall/deck plate it should cut.
        const fc = faceCenter(cell, faceIndex);
        const THK = 0.3;
        const origin: Vec3 = [...fc];
        const size: Vec3 = [0, 0, 0];
        if (side.axis === 2) {
          // Floor/roof face → a hatch: sized in X/Y, thin through the deck.
          const w = 0.9;
          origin[0] = fc[0] - w / 2;
          origin[1] = fc[1] - w / 2;
          origin[2] = fc[2] - THK / 2;
          size[0] = w;
          size[1] = w;
          size[2] = THK;
        } else {
          // Vertical wall face → a door/window: width along the other horizontal
          // axis, height along Z, seated from the cell base.
          const horiz = side.axis === 0 ? 1 : 0;
          const width = subtype === "door" ? 0.9 : 1.2;
          const height = subtype === "door" ? 2.1 : 1.0;
          const z0 = cell.origin[2] + (subtype === "door" ? 0 : 1.0);
          origin[side.axis] = fc[side.axis] - THK / 2;
          origin[horiz] = fc[horiz] - width / 2;
          origin[2] = z0;
          size[side.axis] = THK;
          size[horiz] = width;
          size[2] = height;
        }
        const id = nextId();
        const count =
          Object.values(s.cells).filter((c) => c.kind === "opening").length + 1;
        const opening: BuilderCell = {
          id,
          name: `OPENING_${String(count).padStart(2, "0")}`,
          kind: "opening",
          subtype,
          origin: quantizeVec(origin, s.gridStep),
          size: quantizeVec(size, s.gridStep),
          params: {},
        };
        return {
          cells: { ...s.cells, [id]: opening },
          dirty: true,
          mode: "idle",
          selection: { kind: "cell", cellId: id },
        };
      }),

    updateCell: (id, patch) =>
      withHistory((s) => {
        const cur = s.cells[id];
        if (!cur) return {};
        return {
          cells: { ...s.cells, [id]: { ...cur, ...patch } },
          dirty: true,
        };
      }),
    // Move a space cell to `newOrigin` AND carry the given equipment cells
    // (captured as "contained" when the drag started) by the same delta — so an
    // equipment moves rigidly with the cell it sits in. Per-frame during a
    // translate drag; coalesced into the drag's single undo step via withHistory.
    moveCellAndEquipment: (cellId, newOrigin, equipIds) =>
      withHistory((s) => {
        const cell = s.cells[cellId];
        if (!cell) return {};
        const dx = newOrigin[0] - cell.origin[0];
        const dy = newOrigin[1] - cell.origin[1];
        const dz = newOrigin[2] - cell.origin[2];
        const cells = { ...s.cells, [cellId]: { ...cell, origin: newOrigin } };
        for (const id of equipIds) {
          const e = s.cells[id];
          if (e)
            cells[id] = {
              ...e,
              origin: [e.origin[0] + dx, e.origin[1] + dy, e.origin[2] + dz],
            };
        }
        return { cells, dirty: true };
      }),
    setCellRotation: (id, rotation) =>
      withHistory((s) => {
        const cur = s.cells[id];
        if (!cur || cur.kind !== "equipment") return {};
        const prev = cur.rotation ?? [0, 0, 0];
        // Skip a no-op set so a gizmo that fires objectChange without moving
        // (or the manual panel re-applying the same value) doesn't spawn an
        // empty undo step.
        if (
          prev[0] === rotation[0] &&
          prev[1] === rotation[1] &&
          prev[2] === rotation[2]
        )
          return {};
        return {
          cells: { ...s.cells, [id]: { ...cur, rotation } },
          dirty: true,
        };
      }),
    bumpSelectedFloor: (delta) =>
      withHistory((s) => {
        const sel = s.selection;
        if (!sel) return {};
        const cell = s.cells[sel.cellId];
        // Equipment and openings ride on a floor; a space cell defines the
        // floors itself, so it doesn't bump.
        if (!cell || (cell.kind !== "equipment" && cell.kind !== "opening"))
          return {};
        // Floor levels = the distinct base-Z of the space cells, ascending.
        const floors = Array.from(
          new Set(
            Object.values(s.cells)
              .filter((c) => c.kind === "cell")
              .map((c) => c.origin[2]),
          ),
        ).sort((a, b) => a - b);
        if (floors.length < 2) return {};
        const z = cell.origin[2];
        let ci = 0;
        for (let i = 0; i < floors.length; i++)
          if (floors[i] <= z + 1e-6) ci = i;
        const ti = ci + delta;
        if (ti < 0 || ti >= floors.length) return {}; // no floor that way
        const dz = floors[ti] - floors[ci];
        const origin: Vec3 = [
          cell.origin[0],
          cell.origin[1],
          cell.origin[2] + dz,
        ];
        // Re-home SPACE_NAME to a space cell on the new floor whose XY footprint
        // holds the moved object (best-effort; geometry keys on X/Y/Z, not name).
        const host = Object.values(s.cells).find(
          (c) =>
            c.kind === "cell" &&
            Math.abs(c.origin[2] - floors[ti]) < 1e-6 &&
            c.origin[0] - 1e-6 <= origin[0] &&
            origin[0] <= c.origin[0] + c.size[0] + 1e-6 &&
            c.origin[1] - 1e-6 <= origin[1] &&
            origin[1] <= c.origin[1] + c.size[1] + 1e-6,
        );
        const params = host
          ? { ...cell.params, SPACE_NAME: host.name }
          : cell.params;
        return {
          cells: { ...s.cells, [sel.cellId]: { ...cell, origin, params } },
          dirty: true,
        };
      }),
    renameCell: (id, name) =>
      withHistory((s) => {
        const cur = s.cells[id];
        const trimmed = name.trim();
        if (!cur || !trimmed || trimmed === cur.name) return {};
        // Reject a name already taken by another cell — connections and the
        // compiled entities key on the name, so it must stay unique.
        if (
          Object.values(s.cells).some((c) => c.id !== id && c.name === trimmed)
        )
          return {};
        const cells = { ...s.cells, [id]: { ...cur, name: trimmed } };
        // Equipment names are referenced by system connections; rewrite them
        // in the same history step so a rename never orphans a run.
        let systems = s.systems;
        if (cur.kind === "equipment") {
          systems = Object.fromEntries(
            Object.entries(s.systems).map(([sid, sys]) => [
              sid,
              {
                ...sys,
                connections: sys.connections.map((c) =>
                  c.equipment === cur.name ? { ...c, equipment: trimmed } : c,
                ),
              },
            ]),
          );
        }
        // Enclosure is keyed by cell name too — carry it across a rename.
        let blueprintOptions = s.blueprintOptions;
        const enc = (blueprintOptions as { enclosed_cells?: string[] })
          .enclosed_cells;
        if (
          cur.kind === "cell" &&
          Array.isArray(enc) &&
          enc.includes(cur.name)
        ) {
          blueprintOptions = {
            ...blueprintOptions,
            enclosed_cells: enc.map((n) => (n === cur.name ? trimmed : n)),
          };
        }
        return { cells, systems, blueprintOptions, dirty: true };
      }),
    setCellParam: (id, key, value) =>
      withHistory((s) => {
        const cur = s.cells[id];
        if (!cur) return {};
        const params = { ...cur.params };
        if (value === undefined || value === null || value === "")
          delete params[key];
        else params[key] = value;
        return { cells: { ...s.cells, [id]: { ...cur, params } }, dirty: true };
      }),
    applyFaceExtension: (id, faceIndex, length) => {
      const s = get();
      const cur = s.cells[id];
      const side = BOX_FACE_SIDES[faceIndex];
      if (!cur || !side || !length) return;
      // outward extension of a negative face = negative offset along +axis
      const next = applyFaceOffset(
        cur,
        side.axis,
        side.positive,
        side.positive ? length : -length,
        s.gridStep || 0.1,
      );
      s.updateCell(id, { origin: next.origin, size: next.size });
    },
    setEdgeLength: (id, axis, length) => {
      const s = get();
      const cur = s.cells[id];
      if (!cur || !(length > 0)) return;
      const next = withAxisLength(cur, axis, length, s.gridStep || 0.1);
      s.updateCell(id, { origin: next.origin, size: next.size });
    },
    removeCell: (id) =>
      withHistory((s) => {
        if (!s.cells[id]) return {};
        const cells = { ...s.cells };
        delete cells[id];
        return {
          cells,
          dirty: true,
          selection: s.selection?.cellId === id ? null : s.selection,
          selectedCellIds: s.selectedCellIds.filter((cid) => cid !== id),
          gizmoMode: s.selection?.cellId === id ? "none" : s.gizmoMode,
        };
      }),

    // --- Loft editing (Phase 3a) ---------------------------------------
    // Each: mutate the raw loftMembers (the source of truth), regenerate only
    // that member's band cells (ids preserved by name so selection/gizmo
    // survive), mark dirty, push an undo snapshot (shared with box edits). The
    // edited members round-trip verbatim through toDoc -> loft_members, so a
    // recompile rebuilds the edited geometry from the stations.
    setLoftStationParam: (memberName, stationIndex, key, value) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = setStationParam(member, stationIndex, key, value);
        if (next === member) return {};
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),
    insertLoftStation: (memberName, afterIndex) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = insertStation(member, afterIndex);
        if (next === member) return {};
        const cells = regenLoftMemberCells(s.cells, next);
        // The inserted station keeps bay `afterIndex` as its own lo bay; keep
        // the selection there so the panel stays on the same member.
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells,
          dirty: true,
          ...remapLoftSelection(s, cells, memberName, afterIndex),
        };
      }),
    removeLoftStation: (memberName, stationIndex) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = removeStation(member, stationIndex);
        if (next === member) return {}; // refused (< 2 stations) / out of range
        const cells = regenLoftMemberCells(s.cells, next);
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells,
          dirty: true,
          ...remapLoftSelection(s, cells, memberName, stationIndex - 1),
        };
      }),
    removeLoftMember: (memberName) =>
      withHistory((s) => {
        if (!s.loftMembers.some((m) => m.NAME === memberName)) return {};
        const removed = new Set<string>();
        const cells = { ...s.cells };
        for (const [id, c] of Object.entries(s.cells)) {
          if (c.kind === "loft" && c.loft?.member === memberName) {
            delete cells[id];
            removed.add(id);
          }
        }
        const selGone = s.selection ? removed.has(s.selection.cellId) : false;
        return {
          loftMembers: s.loftMembers.filter((m) => m.NAME !== memberName),
          cells,
          dirty: true,
          selection: selGone ? null : s.selection,
          selectedCellIds: s.selectedCellIds.filter((id) => !removed.has(id)),
          gizmoMode: selGone ? ("none" as GizmoMode) : s.gizmoMode,
        };
      }),
    moveLoftMember: (memberName, delta) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = translateMember(member, delta);
        if (next === member) return {};
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),
    renameLoftMember: (memberName, name) =>
      withHistory((s) => {
        const trimmed = name.trim();
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member || !trimmed || trimmed === memberName) return {};
        if (s.loftMembers.some((m) => m.NAME === trimmed)) return {}; // dup
        const next: LoftMemberDoc = { ...member, NAME: trimmed };
        // Rename shifts every bay cell name, so ids remint — clear the member's
        // cells first (so regen doesn't match stale names) then rebuild.
        const cleared: Record<string, BuilderCell> = {};
        for (const [id, c] of Object.entries(s.cells))
          if (!(c.kind === "loft" && c.loft?.member === memberName))
            cleared[id] = c;
        const cells = regenLoftMemberCells(cleared, next);
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells,
          dirty: true,
          ...remapLoftSelection(s, cells, trimmed, 0),
        };
      }),
    setLoftFaceExcluded: (memberName, faceId, excluded) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = setExcludeFace(member, faceId, excluded);
        if (next === member) return {}; // already in the wanted state — no-op
        // No geometry change (exclude only omits compiled plates), but the band
        // cells carry excludeFaces so the proxy can dim the removed panels —
        // regen so that reaches the controller. Ids stay stable (band count
        // unchanged) so the selection + gizmo survive.
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),

    setLoftMemberMetadata: (memberName, metadata) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = { ...member };
        // Empty -> drop the key entirely (no METADATA={} in the doc).
        if (metadata && Object.keys(metadata).length) next.METADATA = metadata;
        else delete next.METADATA;
        // Metadata is geometry-neutral, so no cell regen — just the raw member.
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          dirty: true,
        };
      }),
    addLoftMember: (base) =>
      withHistory((s) => {
        let n = s.loftMembers.length + 1;
        let name = `LOFT_${String(n).padStart(2, "0")}`;
        while (s.loftMembers.some((m) => m.NAME === name)) {
          n += 1;
          name = `LOFT_${String(n).padStart(2, "0")}`;
        }
        // On a selected face: grow the loft out of that face (rectangle sized to
        // the face, extruded along its normal). Otherwise seed a default circle
        // at the model's ground level so it lands in view (not a far-off z=0).
        const cells0 = Object.values(s.cells);
        const groundZ = cells0.length
          ? Math.min(...cells0.map((c) => c.origin[2]))
          : 0;
        const member = base
          ? seedLoftMemberOnPlane(name, base.placement, base.width, base.height, 3)
          : seedLoftMember(name, [0, 0, groundZ], 3);
        const cells = regenLoftMemberCells(s.cells, member);
        const bay0 = Object.values(cells).find(
          (c) => c.kind === "loft" && c.loft?.member === name && c.loft?.bay === 0,
        );
        return {
          loftMembers: [...s.loftMembers, member],
          cells,
          dirty: true,
          selection: bay0 ? { kind: "cell", cellId: bay0.id } : s.selection,
          selectedCellIds: bay0 ? [bay0.id] : s.selectedCellIds,
        };
      }),
    extendLoftStack: (memberName, spacing) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member || !spacing) return {};
        const stations = member.STATIONS ?? [];
        if (!stations.length) return {};
        const lastIdx = stations.length - 1;
        const topZ = Number(stations[lastIdx].Z);
        // Add a station duplicating the top, then set its exact spine offset.
        let next = insertStation(member, lastIdx);
        next = setStationParam(next, lastIdx + 1, "Z", topZ + spacing);
        if (next === member) return {};
        const cells = regenLoftMemberCells(s.cells, next);
        // Select the new top bay (bay index = the old top station index).
        const topBay = Object.values(cells).find(
          (c) =>
            c.kind === "loft" &&
            c.loft?.member === memberName &&
            c.loft?.bay === lastIdx,
        );
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells,
          dirty: true,
          selection: topBay
            ? { kind: "cell", cellId: topBay.id }
            : s.selection,
          selectedCellIds: topBay ? [topBay.id] : s.selectedCellIds,
        };
      }),
    resizeLoftStation: (memberName, stationIndex, primary) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const station = member.STATIONS?.[stationIndex];
        if (!station) return {};
        let next =
          station.TYPE === "circle"
            ? setStationParam(member, stationIndex, "RADIUS", primary)
            : setStationParam(member, stationIndex, "WIDTH", primary);
        if (station.TYPE !== "circle") {
          next = setStationParam(next, stationIndex, "HEIGHT", primary);
        }
        if (next === member) return {};
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),
    setLoftStationType: (memberName, stationIndex, type) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const station = member.STATIONS?.[stationIndex];
        if (!station || station.TYPE === type) return {};
        const nextStations = member.STATIONS.slice();
        nextStations[stationIndex] = retypeStation(station, type);
        const next: LoftMemberDoc = { ...member, STATIONS: nextStations };
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),

    addSystem: (type, opts) =>
      withHistory((s) => {
        const id = nextId();
        const count = Object.keys(s.systems).length + 1;
        const prefix = {
          piping: "PIPE",
          duct: "DUCT",
          cable: "CABLE",
          electrical: "POWER",
        }[type];
        const name =
          opts?.name ?? `${prefix}_${String(count).padStart(2, "0")}`;
        const system: BuilderSystem = { id, name, type, connections: [] };
        if (opts?.medium) system.medium = opts.medium;
        return { systems: { ...s.systems, [id]: system }, dirty: true };
      }),
    updateSystem: (id, patch) =>
      withHistory((s) => {
        const cur = s.systems[id];
        if (!cur) return {};
        return {
          systems: { ...s.systems, [id]: { ...cur, ...patch } },
          dirty: true,
        };
      }),
    removeSystem: (id) =>
      withHistory((s) => {
        if (!s.systems[id]) return {};
        const systems = { ...s.systems };
        delete systems[id];
        return { systems, dirty: true };
      }),
    addSystemConnection: (id, conn) =>
      withHistory((s) => {
        const cur = s.systems[id];
        if (!cur) return {};
        return {
          systems: {
            ...s.systems,
            [id]: { ...cur, connections: [...cur.connections, conn] },
          },
          dirty: true,
        };
      }),
    removeSystemConnection: (id, index) =>
      withHistory((s) => {
        const cur = s.systems[id];
        if (!cur) return {};
        return {
          systems: {
            ...s.systems,
            [id]: {
              ...cur,
              connections: cur.connections.filter((_, i) => i !== index),
            },
          },
          dirty: true,
        };
      }),
    systemsForEquipment: (equipmentName) =>
      Object.values(get().systems).filter((sys) =>
        sys.connections.some((c) => c.equipment === equipmentName),
      ),

    toDoc: () => {
      const cells = get().cells;
      const spaces = Object.values(cells)
        .filter((c) => c.kind === "cell")
        .map((c) => {
          // A grouped cell stamps its group as the space's STRUCTURE_NAME (the pm
          // engine reads it back to route the cell into that group's structure);
          // ungrouped cells omit the key entirely (backward compatible).
          const structureName = groupToStructureName(c.group);
          return {
            INCLUDE: true,
            ...c.params,
            NAME: c.name,
            X: c.origin[0],
            Y: c.origin[1],
            Z: c.origin[2],
            DX: c.size[0],
            DY: c.size[1],
            DZ: c.size[2],
            ...(structureName ? { STRUCTURE_NAME: structureName } : {}),
          };
        });
      const equipments = Object.values(cells)
        .filter((c) => c.kind === "equipment")
        .map((c) => ({
          INCLUDE: true,
          SPACE_NAME: containingCellName(cells, c),
          SPACE_LOC: "ROOF",
          COGx: 0,
          COGy: 0,
          COGz: c.size[2] / 2,
          massDry: 0,
          massCont: 0,
          ...c.params,
          NAME: c.name,
          GLOBAL_COORDS: true,
          DESCRIPTION: c.equipmentType ?? null,
          X: c.origin[0],
          Y: c.origin[1],
          Z: c.origin[2],
          LX: c.size[0],
          LY: c.size[1],
          LZ: c.size[2],
          ROT_X: c.rotation?.[0] ?? 0,
          ROT_Y: c.rotation?.[1] ?? 0,
          ROT_Z: c.rotation?.[2] ?? 0,
        }));
      const openings = Object.values(cells)
        .filter((c) => c.kind === "opening")
        .map((c) => ({
          INCLUDE: true,
          ...c.params,
          NAME: c.name,
          SUBTYPE: c.subtype ?? "door",
          USE_GLOBAL_COORDS: true,
          X: c.origin[0],
          Y: c.origin[1],
          Z: c.origin[2],
          DX: c.size[0],
          DY: c.size[1],
          DZ: c.size[2],
        }));
      const systems = Object.values(get().systems).map((sys) => ({
        NAME: sys.name,
        TYPE: sys.type,
        MEDIUM: sys.medium ?? null,
        CONNECTIONS: sys.connections.map((c) =>
          c.site
            ? {
                SITE: c.site,
                POSITION: c.position ?? [0, 0, 0],
                DIRECTION: c.direction ?? "IN",
                DIRECTION_VECTOR: c.directionVector ?? [0, 0, 1],
              }
            : { EQUIPMENT: c.equipment, PORT: c.port },
        ),
      }));
      // Loft members carry the (Phase 3a) station/placement edits — emit the
      // live array, only when present so box-only docs stay byte-identical
      // (mirrors the backend's conditional dump).
      const loftMembers = get().loftMembers;
      // Cell groups (each with its own blueprint), normalized. Emitted only when
      // present so an ungrouped model's doc stays byte-identical (a
      // grouping-unaware engine ignores the key regardless).
      const groups = normalizeGroups(get().groups);
      return {
        grid: {},
        blueprint: get().blueprintOptions,
        // The selected structural blueprint (kept OUT of the whitelisted
        // `blueprint` options); a legacy/absent selection defaults to steel_stru.
        blueprint_name: get().selectedBlueprint ?? "steel_stru",
        design_rules: get().designRules,
        // Persist the fabrication-detail selection so the model (or a template)
        // carries its detailing intent across open/commit ("none" stays absent to
        // keep a structural-only doc byte-identical).
        ...(get().selectedDetailing && get().selectedDetailing !== "none"
          ? { detailing: get().selectedDetailing }
          : {}),
        equipment_cad: get().equipmentCad,
        spaces,
        equipments,
        systems,
        openings,
        ...(loftMembers.length ? { loft_members: loftMembers } : {}),
        ...(groups.length ? { groups } : {}),
      };
    },
    loadFromDoc: (doc) =>
      set({
        cells: cellsFromDoc(doc),
        loftMembers: loftMembersFromDoc(doc),
        systems: systemsFromDoc(doc),
        groups: groupsFromDoc(doc),
        blueprintOptions: doc.blueprint ?? {},
        equipmentCad: Boolean(doc.equipment_cad),
        designRules: doc.design_rules ?? "standard",
        selectedBlueprint: doc.blueprint_name ?? "steel_stru",
        selectedDetailing: doc.detailing ?? "none",
        past: [],
        future: [],
        txDepth: 0,
        dirty: false,
        selection: null,
      }),

    undo: () =>
      set((s) => {
        const step = undoStep(s, snapshot(s), HISTORY_LIMIT);
        if (!step) return {};
        return {
          ...step.restored,
          ...step.stacks,
          // `open` and `commit` reset `past: []`, so an empty past means we're
          // back at the last save-point — i.e. no uncommitted changes. Undoing
          // all the way therefore clears dirty (Compile/Commit disable again),
          // instead of leaving the model perpetually dirty.
          dirty: step.stacks.past.length > 0,
          selection: pruneSelection(s.selection, step.restored.cells),
          selectedCellIds: s.selectedCellIds.filter(
            (id) => step.restored.cells[id],
          ),
        };
      }),
    redo: () =>
      set((s) => {
        const step = redoStep(s, snapshot(s), HISTORY_LIMIT);
        if (!step) return {};
        return {
          ...step.restored,
          ...step.stacks,
          dirty: step.stacks.past.length > 0,
          selection: pruneSelection(s.selection, step.restored.cells),
          selectedCellIds: s.selectedCellIds.filter(
            (id) => step.restored.cells[id],
          ),
        };
      }),
    beginTransaction: () =>
      set((s) => {
        // capture the pre-burst snapshot once, at the outermost begin
        if (s.txDepth === 0)
          return { txDepth: 1, ...pushSnapshot(s, snapshot(s), HISTORY_LIMIT) };
        return { txDepth: s.txDepth + 1 };
      }),
    endTransaction: () => set((s) => ({ txDepth: Math.max(0, s.txDepth - 1) })),

    fetchEquipmentTypes: async () => {
      try {
        const types =
          await viewerApi.proceduralEquipmentTypes(currentScopePart());
        set((s) => ({
          equipmentTypes: types,
          selectedEquipmentType:
            s.selectedEquipmentType &&
            types.some((t) => t.slug === s.selectedEquipmentType)
              ? s.selectedEquipmentType
              : (types[0]?.slug ?? null),
        }));
      } catch (e) {
        console.warn("cellbuilder: equipment-types fetch failed", e);
        set({ equipmentTypes: [] });
      }
    },

    fetchCellTypes: async () => {
      try {
        const types = await viewerApi.proceduralCellTypes(currentScopePart());
        set((s) => ({
          cellTypes: types,
          selectedCellType:
            s.selectedCellType &&
            types.some((t) => t.slug === s.selectedCellType)
              ? s.selectedCellType
              : (types[0]?.slug ?? null),
        }));
      } catch (e) {
        console.warn("cellbuilder: cell-types fetch failed", e);
        set({ cellTypes: [] });
      }
    },

    fetchOpeningTypes: async () => {
      try {
        const types = await viewerApi.proceduralOpeningTypes(currentScopePart());
        set((s) => ({
          openingTypes: types,
          selectedOpeningType:
            s.selectedOpeningType &&
            types.some((t) => t.slug === s.selectedOpeningType)
              ? s.selectedOpeningType
              : (types[0]?.slug ?? null),
        }));
      } catch (e) {
        console.warn("cellbuilder: opening-types fetch failed", e);
        set({ openingTypes: [] });
      }
    },

    fetchSystemTypes: async () => {
      try {
        const types = await viewerApi.proceduralSystemTypes(currentScopePart());
        set({ systemTypes: types });
      } catch (e) {
        console.warn("cellbuilder: system-types fetch failed", e);
        set({ systemTypes: [] });
      }
    },

    fetchDesignRulesets: async () => {
      try {
        const rulesets =
          await viewerApi.proceduralDesignRulesets(currentScopePart());
        set({ designRulesets: rulesets });
      } catch (e) {
        console.warn("cellbuilder: design-rulesets fetch failed", e);
        set({ designRulesets: [] });
      }
    },

    // Engine selection is a compile-time choice, not part of the model document
    // — no history / no doc round-trip; just picks which engine the next compile
    // dispatches to (server and in-browser both resolve it identically). The
    // offered BLUEPRINTS are engine-scoped, so a change refetches them and
    // reconciles the selection (to the new engine's default if the current one
    // isn't offered).
    setSelectedEngine: (slug) => {
      set({ selectedEngine: slug || "adapy-default" });
      void get().fetchBlueprints();
    },

    fetchEngines: async () => {
      try {
        const engines =
          await viewerApi.listProceduralEngines(currentScopePart());
        set({ engines });
      } catch (e) {
        console.warn("cellbuilder: engines fetch failed", e);
        set({ engines: [] });
      }
    },

    // Detailing selection is a compile-time choice too (not part of the document):
    // it picks the fabrication-detail engine the next compile applies after the
    // structural build. "none" = structural-only (byte-identical to today).
    setSelectedDetailing: (slug) => {
      const next = slug || "none";
      set((s) => {
        // Reconcile the per-joint options against the NEWLY-selected engine's
        // advertised specs (mirror how the blueprint selection reconciles on an
        // engine change): keep still-valid edits, default new joints, drop gone
        // ones. "none" advertises nothing -> empty map.
        const engine = s.detailingEngines.find((e) => e.slug === next);
        return {
          selectedDetailing: next,
          detailingOptions: resolveDetailingOptions(engine, s.detailingOptions),
        };
      });
    },

    fetchDetailingEngines: async () => {
      try {
        const detailingEngines =
          await viewerApi.listDetailingEngines(currentScopePart());
        // Re-reconcile the current selection's options against the freshly
        // advertised specs (a worker may advertise more/other joint types than
        // the static fallback the first fetch saw).
        set((s) => {
          const engine = detailingEngines.find(
            (e) => e.slug === s.selectedDetailing,
          );
          return {
            detailingEngines,
            detailingOptions: resolveDetailingOptions(
              engine,
              s.detailingOptions,
            ),
          };
        });
      } catch (e) {
        console.warn("cellbuilder: detailing engines fetch failed", e);
        set({ detailingEngines: [], detailingOptions: {} });
      }
    },

    setDetailingJointEnabled: (jointSlug, enabled) =>
      set((s) => {
        const prev = s.detailingOptions[jointSlug];
        if (!prev || prev.enabled === enabled) return {};
        return {
          detailingOptions: {
            ...s.detailingOptions,
            [jointSlug]: { ...prev, enabled },
          },
        };
      }),

    setDetailingField: (jointSlug, fieldName, value) =>
      set((s) => {
        const prev = s.detailingOptions[jointSlug];
        if (!prev) return {};
        return {
          detailingOptions: {
            ...s.detailingOptions,
            [jointSlug]: {
              ...prev,
              fields: { ...prev.fields, [fieldName]: value },
            },
          },
        };
      }),

    detailingOptionsPayload: () => {
      const s = get();
      if (s.selectedDetailing === "none") return null;
      const engine = s.detailingEngines.find(
        (e) => e.slug === s.selectedDetailing,
      );
      return toDetailingOptionsPayload(engine, s.detailingOptions);
    },

    // Picking a blueprint changes the document (doc.blueprint_name), so it marks
    // the model dirty — unlike the compile-time engine/ruleset toggles.
    setSelectedBlueprint: (slug) =>
      set((s) =>
        slug === s.selectedBlueprint
          ? {}
          : { selectedBlueprint: slug, dirty: true },
      ),

    setBlueprintOption: (name, value) =>
      set((s) => ({
        blueprintOptions: { ...s.blueprintOptions, [name]: value },
        dirty: true,
      })),

    fetchBlueprints: async () => {
      try {
        const blueprints = await viewerApi.proceduralBlueprints(
          currentScopePart(),
          get().selectedEngine,
        );
        set((s) => ({
          blueprints,
          selectedBlueprint: resolveSelectedBlueprint(
            blueprints,
            s.selectedBlueprint,
          ),
        }));
      } catch (e) {
        console.warn("cellbuilder: blueprints fetch failed", e);
        set({ blueprints: [] });
      }
    },

    addGroup: () =>
      withHistory((s) => {
        // Auto-name "Group N" avoiding collisions; default the new group's
        // blueprint to the current selection (else the engine's first offered).
        const existing = new Set(s.groups.map((g) => g.name));
        let n = s.groups.length + 1;
        let name = `Group ${n}`;
        while (existing.has(name)) name = `Group ${++n}`;
        const blueprint = s.selectedBlueprint ?? s.blueprints[0]?.slug ?? "";
        return { groups: [...s.groups, { name, blueprint }], dirty: true };
      }),

    renameGroup: (from, to) =>
      withHistory((s) => {
        const name = to.trim();
        // Ignore blank / unchanged / colliding names, or a missing source.
        if (
          !name ||
          name === from ||
          s.groups.some((g) => g.name === name) ||
          !s.groups.some((g) => g.name === from)
        )
          return {};
        const groups = s.groups.map((g) => (g.name === from ? { ...g, name } : g));
        // Repoint every cell that referenced the old name.
        const cells: Record<string, BuilderCell> = {};
        for (const [id, c] of Object.entries(s.cells))
          cells[id] = c.group === from ? { ...c, group: name } : c;
        return { groups, cells, dirty: true };
      }),

    removeGroup: (name) =>
      withHistory((s) => {
        if (!s.groups.some((g) => g.name === name)) return {};
        const groups = s.groups.filter((g) => g.name !== name);
        const removed = new Set([name]);
        // Unassign every cell in the deleted group (back to ungrouped).
        const cells: Record<string, BuilderCell> = {};
        for (const [id, c] of Object.entries(s.cells))
          cells[id] = { ...c, group: groupAfterRemoval(c.group, removed) };
        return { groups, cells, dirty: true };
      }),

    setGroupBlueprint: (name, blueprint) =>
      withHistory((s) => {
        if (!s.groups.some((g) => g.name === name && g.blueprint !== blueprint))
          return {};
        return {
          groups: s.groups.map((g) => (g.name === name ? { ...g, blueprint } : g)),
          dirty: true,
        };
      }),

    setCellGroup: (cellId, groupName) =>
      withHistory((s) => {
        const cell = s.cells[cellId];
        if (!cell) return {};
        const group = groupName && groupName.trim() ? groupName : undefined;
        // Accept only an existing group (or clearing to ungrouped).
        if (group && !s.groups.some((g) => g.name === group)) return {};
        if (cell.group === group) return {};
        return {
          cells: { ...s.cells, [cellId]: { ...cell, group } },
          dirty: true,
        };
      }),

    syncEquipmentTypeToDb: async (slug) => {
      try {
        await viewerApi.syncProceduralEquipmentType(currentScopePart(), slug);
        await get().fetchEquipmentTypes();
      } catch (e) {
        console.warn("cellbuilder: equipment-type sync failed", e);
      }
    },

    syncSystemTypeToDb: async (slug) => {
      try {
        await viewerApi.syncProceduralSystemType(currentScopePart(), slug);
        await get().fetchSystemTypes();
      } catch (e) {
        console.warn("cellbuilder: system-type sync failed", e);
      }
    },

    resyncEquipmentTypes: async (opts) => {
      const quiet = opts?.quiet ?? false;
      if (!quiet) {
        if (get().resyncBusy) return null; // already running (button also disables)
        set({ resyncBusy: true, resyncSummary: null });
        // Show the toast immediately (the upsert is quick but the round-trip isn't
        // instant) so the click gives feedback instead of appearing to do nothing.
        setProceduralToast("Resync equipments", {
          status: "running",
          progress: 0,
          stage: "syncing catalog…",
          startedAt: Date.now(),
        });
      }
      try {
        const res =
          await viewerApi.resyncProceduralEquipmentTypes(currentScopePart());
        await get().fetchEquipmentTypes();
        const changed = res.created.length + res.updated.length;
        // Announce on the global toast unless this was a silent auto-resync that
        // found nothing to change (avoid noise on every model open).
        if (!quiet || changed > 0) {
          setProceduralToast("Resync equipments", {
            status: "done",
            progress: 1,
            stage: changed
              ? `${res.updated.length} updated, ${res.created.length} added`
              : "catalog already up to date",
          });
        }
        // A user-triggered resync also opens a summary popup detailing which
        // equipment changed and how (a quiet auto-resync stays silent).
        if (!quiet) set({ resyncSummary: res });
        return res;
      } catch (e) {
        console.warn("cellbuilder: equipment resync failed", e);
        if (!quiet)
          setProceduralToast("Resync equipments", {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          });
        return null;
      } finally {
        if (!quiet) set({ resyncBusy: false });
      }
    },

    dismissResyncSummary: () => set({ resyncSummary: null }),

    commit: async () => {
      const s = get();
      if (!s.active || s.committing) return false;
      set({ committing: true, conflict: null });
      try {
        const res = await viewerApi.commitProceduralModel(
          currentScopePart(),
          s.active.modelId,
          s.toDoc(),
          s.active.revision,
        );
        set({
          active: { ...s.active, revision: res.revision },
          dirty: false,
          committing: false,
        });
        if (get().autoCompile) {
          void get().compile();
        }
        return true;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          set({
            committing: false,
            conflict:
              "Commit conflict: the model changed elsewhere. Reload it to pick up the latest revision.",
          });
        } else {
          set({
            committing: false,
            conflict: e instanceof Error ? e.message : String(e),
          });
        }
        return false;
      }
    },

    compile: async (force = false, lod = "sim") => {
      // A COMMITTED build: persists the revision (if dirty) then builds the
      // revision-keyed GLB. This is what commit()'s auto-compile and the detail
      // view use; interactive previewing is compilePreview() (no commit).
      const s = get();
      if (!s.active) return;
      if (s.dirty) {
        const ok = await get().commit();
        // commit() auto-compiles on success when enabled; avoid double-run.
        // A forced recompile still proceeds — the auto-compile after commit is a
        // normal (cache-honouring) run, so we fall through to re-run with force.
        // The auto-compile only covers the SIMULATION lod, so a detail request
        // must still proceed after a commit to build its own artifact.
        if (ok && get().autoCompile && !force && lod === "sim") return;
        if (!ok) return;
      }
      const active = get().active;
      if (!active) return;
      const label = lod === "detail" ? `${active.name} (detail)` : active.name;
      await startCompileJob(label, lod, () =>
        viewerApi.compileProceduralModel(
          currentScopePart(),
          active.modelId,
          force,
          lod,
          get().selectedEngine,
          get().selectedDetailing,
          get().detailingOptionsPayload(),
        ),
      );
    },

    compilePreview: async (force = false, lod = "sim") => {
      // Build the CURRENT (uncommitted) document as an ephemeral preview — no
      // commit, no revision bump. The server keys the GLB on the doc's content
      // hash, so re-previewing an unchanged doc is free; committing later
      // promotes this exact blob to the revision. This is the interactive
      // visualise-then-commit loop (and the ⇧↵ shortcut / side-by-side driver).
      const active = get().active;
      if (!active) return;
      const doc = get().toDoc();
      const label = `${active.name} (preview)`;
      await startCompileJob(label, lod, () =>
        viewerApi.previewProceduralModel(
          currentScopePart(),
          active.modelId,
          doc,
          {
            engine: get().selectedEngine,
            lod,
            force,
            detailing: get().selectedDetailing,
            detailingOptions: get().detailingOptionsPayload(),
          },
        ),
      );
    },

    compilePreviewSelected: async (force = false) => {
      // Build whichever LOD(s) the user selected (defaulting to simulation if
      // somehow neither is on). Sequential so the two jobs don't contend; each
      // shows in its own view (autoShow renders only the active view's LOD).
      const { buildSim, buildDetail } = get();
      const sim = buildSim || !buildDetail;
      if (sim) await get().compilePreview(force, "sim");
      if (buildDetail) await get().compilePreview(force, "detail");
    },

    setBuildSim: (on) => set({ buildSim: on }),
    setBuildDetail: (on) => set({ buildDetail: on }),

    viewResult: async (derivedKey: string, lod = "sim", explicitSourceName?: string) => {
      const active = get().active;
      const base = active ? active.name : derivedKey;
      // Simulation and detail are distinct scene sources so they never collide.
      const sourceName =
        explicitSourceName ??
        (lod === "detail" ? `procedural-detail:${base}` : `procedural:${base}`);
      const { load_glb_by_url_rest } = await import(
        "@/utils/scene/handlers/view_file_object_from_server"
      );
      // autoFit=false: a compile/recompile must never move the camera.
      await load_glb_by_url_rest(currentScopePart(), derivedKey, sourceName, false);
      set(
        lod === "detail"
          ? { detailSourceName: sourceName }
          : { resultSourceName: sourceName },
      );
      // Side-by-side: nudge the freshly-loaded result beside the topology. The
      // loader replaces the group (position resets to the model translation), so
      // re-apply on every load.
      if (get().sideBySide) {
        const topoMaxX = modelMaxX(get().cells);
        void import("@/utils/scene/handlers/side_by_side").then(
          ({ applySideBySideOffset }) =>
            applySideBySideOffset(sourceName, true, topoMaxX),
        );
      }
    },

    compileInBrowser: async () => {
      const active = get().active;
      if (!active) return;
      const label = `${active.name} (browser)`;
      const doc = get().toDoc();
      setProceduralToast(label, {
        status: "running",
        stage: "compiling in browser…",
        progress: 0,
        startedAt: Date.now(),
      });
      try {
        const { compileProceduralViaPyodide } = await import(
          "@/utils/pyodide/pyodide_converter"
        );
        const { load_glb_from_bytes } = await import(
          "@/utils/scene/handlers/view_file_object_from_server"
        );
        // Resolve the engine for the browser: a built-in slug (e.g. echo)
        // dispatches directly; a kind:wheel engine is micropip-installed from its
        // presigned wheel URL then dispatched via its entrypoint; a server engine
        // can't run in-browser.
        const engineSlug = get().selectedEngine;
        let engineArg: string | null =
          engineSlug && engineSlug !== "adapy-default" ? engineSlug : null;
        let wheel: { entrypoint: string; deps: string[]; url: string } | null =
          null;
        if (engineArg) {
          const eng = get().engines.find((e) => e.slug === engineSlug);
          if (eng && eng.origin !== "builtin") {
            const resolved = await viewerApi.resolveProceduralEngine(
              currentScopePart(),
              eng.id,
            );
            if (resolved.kind === "wheel") {
              if (
                !resolved.ready ||
                !resolved.wheel_url ||
                !resolved.entrypoint
              )
                throw new Error(
                  "engine wheel is not built yet — try again shortly",
                );
              wheel = {
                entrypoint: resolved.entrypoint,
                deps: resolved.pyodide_deps ?? [],
                url: resolved.wheel_url,
              };
              engineArg = null; // dispatch happens via the wheel entrypoint
            } else if (resolved.kind === "server") {
              throw new Error(
                "this engine runs server-side only — use Compile, not in-browser",
              );
            } else if (resolved.entrypoint) {
              engineArg = resolved.entrypoint;
            }
          }
        }
        const bytes = await compileProceduralViaPyodide(doc, {
          onLog: (m) => setProceduralToast(label, { stage: m }),
          engine: engineArg,
          wheel,
        });
        const sourceName = `procedural:${active.name}`;
        await load_glb_from_bytes(bytes, sourceName, false); // never move the camera
        set({ resultSourceName: sourceName });
        if (get().sideBySide) {
          const { applySideBySideOffset } = await import(
            "@/utils/scene/handlers/side_by_side"
          );
          applySideBySideOffset(sourceName, true, modelMaxX(get().cells));
        }
        setProceduralToast(label, {
          status: "done",
          progress: 1,
          stage: "rendered in browser",
        });
      } catch (e) {
        setProceduralToast(label, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
          stage: "browser compile failed",
        });
      }
    },

    hideResult: () => {
      const sourceName = get().resultSourceName;
      if (!sourceName) return;
      void import("@/utils/scene/handlers/unload_source_from_scene").then(
        ({ unload_source_from_scene }) => {
          unload_source_from_scene(sourceName);
        },
      );
      set({ resultSourceName: null });
    },

    hideDetail: () => {
      const sourceName = get().detailSourceName;
      if (!sourceName) return;
      void import("@/utils/scene/handlers/unload_source_from_scene").then(
        ({ unload_source_from_scene }) => {
          unload_source_from_scene(sourceName);
        },
      );
      set({ detailSourceName: null });
    },

    // The 3-way representation switch: topology cells / the simulation result GLB /
    // the detail result GLB. One result GLB is live at a time (the scene's source
    // map replaces on load, so switching result modes unloads the other); the
    // topology cells are a separate layer, kept visible underneath when
    // `superimpose` is on. Each result GLB is compiled/loaded lazily the first
    // time its view is opened.
    setRepMode: async (mode) => {
      if (get().repMode === mode) return;
      set({ repMode: mode });
      if (mode === "topology") {
        get().setCellsVisible(true);
        get().hideResult();
        get().hideDetail();
        return;
      }
      // Result modes: the topology layer stays visible when superimposing OR
      // showing side-by-side (there it sits beside the result).
      get().setCellsVisible(get().superimpose || get().sideBySide);
      // Build the result as a PREVIEW of the current (uncommitted) state — opening
      // a result view must never force a commit; the user commits when happy.
      if (mode === "simulation") {
        get().hideDetail();
        if (get().resultSourceName === null) await get().compilePreview(false, "sim");
      } else {
        get().hideResult();
        if (get().detailSourceName === null)
          await get().compilePreview(false, "detail");
      }
    },

    // Superimpose the topology cells under the active result. Topology is the base
    // layer, so from topology mode turning it on brings the simulation result up
    // ON TOP (the natural "result over topology" starting point); from a result
    // mode it just toggles the cell layer beneath. Turning it off in a result mode
    // returns to the result on its own.
    setSuperimpose: async (on) => {
      set({ superimpose: on });
      if (get().repMode === "topology") {
        if (on) await get().setRepMode("simulation");
        return;
      }
      // Side-by-side already keeps the cells visible beside the result.
      get().setCellsVisible(on || get().sideBySide);
    },

    setSideBySide: (on) => {
      set({ sideBySide: on });
      if (on) {
        // Keep the editable topology visible beside the result.
        get().setCellsVisible(true);
      } else if (get().repMode === "topology") {
        // Back to a pure Topology view: drop the result that sat beside it so the
        // topology view shows only topology.
        get().hideResult();
        get().hideDetail();
      } else {
        // In a result view, restore the superimpose choice for the cell layer.
        get().setCellsVisible(get().superimpose);
      }
      const topoMaxX = modelMaxX(get().cells);
      const apply = (src: string | null) => {
        if (!src) return;
        void import("@/utils/scene/handlers/side_by_side").then(
          ({ applySideBySideOffset }) => applySideBySideOffset(src, on, topoMaxX),
        );
      };
      apply(get().resultSourceName);
      apply(get().detailSourceName);
    },

    proposeRelocations: async () => {
      const s = get();
      if (!s.active || s.relocationBusy) return;
      // The worker analyses the COMMITTED doc from postgres, so flush edits first.
      if (s.dirty) {
        const ok = await get().commit();
        if (!ok) return;
      }
      const active = get().active;
      if (!active) return;
      const label = active.name;
      set({ relocationBusy: true, relocations: null });
      setProceduralToast(`Relocations: ${label}`, {
        status: "queued",
        stage: "analyzing",
        progress: 0,
        startedAt: Date.now(),
      });
      try {
        const res = await viewerApi.proposeProceduralRelocations(
          currentScopePart(),
          active.modelId,
        );
        if (!res.job_id) {
          const data = await viewerApi.fetchProceduralRelocations(
            currentScopePart(),
            res.derived_key,
          );
          set({ relocations: data, relocationBusy: false });
          setProceduralToast(`Relocations: ${label}`, {
            status: "done",
            progress: 1,
            stage: `${data.proposals.length} proposed`,
          });
          return;
        }
        const jobId = res.job_id;
        const poll = async () => {
          try {
            const st = await viewerApi.convertStatus(jobId);
            if (st.status === "done") {
              const data = await viewerApi.fetchProceduralRelocations(
                currentScopePart(),
                st.derived_key || res.derived_key,
              );
              set({ relocations: data, relocationBusy: false });
              setProceduralToast(`Relocations: ${label}`, {
                status: "done",
                progress: 1,
                stage: data.proposals.length
                  ? `${data.proposals.length} proposed`
                  : "no moves needed",
              });
              return;
            }
            if (st.status === "error") {
              set({ relocationBusy: false });
              setProceduralToast(`Relocations: ${label}`, {
                status: "error",
                error: st.error ?? "relocation analysis failed",
              });
              return;
            }
            setProceduralToast(`Relocations: ${label}`, {
              status: "running",
              progress: st.progress ?? 0,
              stage: st.stage || "analyzing",
            });
            setTimeout(poll, 1500);
          } catch (e) {
            set({ relocationBusy: false });
            setProceduralToast(`Relocations: ${label}`, {
              status: "error",
              error: e instanceof Error ? e.message : String(e),
            });
          }
        };
        setTimeout(poll, 1500);
      } catch (e) {
        set({ relocationBusy: false });
        setProceduralToast(`Relocations: ${label}`, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },

    applyRelocations: () => {
      const proposals = get().relocations?.proposals ?? [];
      if (proposals.length === 0) return;
      const byName = new Map(
        Object.values(get().cells).map((c) => [c.name, c] as const),
      );
      // One undo step for the whole apply.
      get().beginTransaction();
      for (const p of proposals) {
        const cell = byName.get(p.equipment);
        if (!cell || cell.kind !== "equipment") continue;
        // Proposal from/to are equipment ORIGINS (X+LX/2, Y+LY/2, Z); convert
        // back to the box corner the store keeps.
        get().updateCell(cell.id, {
          origin: [
            p.to[0] - cell.size[0] / 2,
            p.to[1] - cell.size[1] / 2,
            p.to[2],
          ],
        });
      }
      get().endTransaction();
      set({ relocations: null });
    },

    // ── Excel round-trip ──────────────────────────────────────────────
    exportToExcel: async () => {
      const active = get().active;
      if (!active || get().xlsxBusy) return;
      const scope = currentScopePart();
      const engine = get().selectedEngine;
      const label = "Export to Excel";
      set({ xlsxBusy: true });
      setProceduralToast(label, {
        status: "running",
        progress: 0,
        stage: "exporting…",
        startedAt: Date.now(),
      });
      const download = async (derivedKey: string) => {
        try {
          await viewerApi.downloadBlob(
            scope,
            derivedKey,
            `${active.name || "procedural-model"}.xlsx`,
          );
          setProceduralToast(label, {
            status: "done",
            progress: 1,
            stage: "downloaded",
          });
        } catch (e) {
          setProceduralToast(label, {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          });
        } finally {
          set({ xlsxBusy: false });
        }
      };
      try {
        // Export the COMMITTED revision (the worker reads the DB doc); commit any
        // unsaved edits first so the workbook matches what's on screen.
        if (get().dirty) await get().commit();
        const res = await viewerApi.exportProceduralModelXlsx(
          scope,
          active.modelId,
          { engine },
        );
        if (res.cached || !res.job_id) {
          await download(res.derived_key);
          return;
        }
        const jobId = res.job_id;
        const poll = async () => {
          try {
            const st = await viewerApi.convertStatus(jobId);
            if (st.status === "done") {
              await download(st.derived_key || res.derived_key);
              return;
            }
            if (st.status === "error") {
              set({ xlsxBusy: false });
              setProceduralToast(label, {
                status: "error",
                stage: st.stage || "",
                error: st.error ?? "export failed",
              });
              return;
            }
            setProceduralToast(label, {
              status: "running",
              progress: st.progress ?? 0,
              stage: st.stage || "exporting…",
              jobId,
            });
            setTimeout(poll, 1500);
          } catch (e) {
            set({ xlsxBusy: false });
            setProceduralToast(label, {
              status: "error",
              error: e instanceof Error ? e.message : String(e),
            });
          }
        };
        setTimeout(poll, 1200);
      } catch (e) {
        set({ xlsxBusy: false });
        setProceduralToast(label, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },

    setExportIfcCad: (v) => set({ exportIfcCad: v }),

    exportModel: async (format) => {
      const active = get().active;
      if (!active || get().xlsxBusy) return;
      const scope = currentScopePart();
      const label =
        format === "ifc" ? "Download IFC (detail)" : "Download Genie XML (sim)";
      set({ xlsxBusy: true });
      setProceduralToast(label, {
        status: "running",
        progress: 0,
        stage: "compiling…",
        startedAt: Date.now(),
      });
      const download = async (derivedKey: string) => {
        try {
          await viewerApi.downloadBlob(
            scope,
            derivedKey,
            `${active.name || "procedural-model"}.${format}`,
          );
          setProceduralToast(label, {
            status: "done",
            progress: 1,
            stage: "downloaded",
          });
        } catch (e) {
          setProceduralToast(label, {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          });
        } finally {
          set({ xlsxBusy: false });
        }
      };
      try {
        // Export the COMMITTED revision (the worker reads the DB doc); commit any
        // unsaved edits first so the file matches what's on screen.
        if (get().dirty) await get().commit();
        const res = await viewerApi.exportProceduralModel(
          scope,
          active.modelId,
          format,
          format === "ifc" ? { cad: get().exportIfcCad } : undefined,
        );
        if (res.cached || !res.job_id) {
          await download(res.derived_key);
          return;
        }
        const jobId = res.job_id;
        const poll = async () => {
          try {
            const st = await viewerApi.convertStatus(jobId);
            if (st.status === "done") {
              await download(st.derived_key || res.derived_key);
              return;
            }
            if (st.status === "error") {
              set({ xlsxBusy: false });
              setProceduralToast(label, {
                status: "error",
                stage: st.stage || "",
                error: st.error ?? "export failed",
              });
              return;
            }
            setProceduralToast(label, {
              status: "running",
              progress: st.progress ?? 0,
              stage: st.stage || "exporting…",
              jobId,
            });
            setTimeout(poll, 1500);
          } catch (e) {
            set({ xlsxBusy: false });
            setProceduralToast(label, {
              status: "error",
              error: e instanceof Error ? e.message : String(e),
            });
          }
        };
        setTimeout(poll, 1200);
      } catch (e) {
        set({ xlsxBusy: false });
        setProceduralToast(label, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },

    beginImportFromExcel: async (file: File) => {
      if (get().xlsxBusy) return;
      const scope = currentScopePart();
      const label = "Import from Excel";
      // Derive a model name from the file (drop the extension).
      const name = file.name.replace(/\.[^.]+$/, "").trim() || "Imported model";
      set({ xlsxBusy: true, importPrompt: null });
      setProceduralToast(label, {
        status: "running",
        progress: 0,
        stage: "uploading…",
        startedAt: Date.now(),
      });
      try {
        const buf = await file.arrayBuffer();
        const detect = await viewerApi.uploadProceduralImportXlsx(scope, buf);
        if (detect.engine) {
          // Engine known from the workbook's _ADA_META — import straight away.
          await runImport(detect.source_key, detect.engine, name);
        } else {
          // No metadata (hand-made / legacy workbook): ask which engine to use.
          await get().fetchEngines();
          setProceduralToast(label, {
            status: "running",
            progress: 0.2,
            stage: "choose an engine…",
          });
          set({
            xlsxBusy: false,
            importPrompt: { sourceKey: detect.source_key, name },
          });
        }
      } catch (e) {
        set({ xlsxBusy: false });
        setProceduralToast(label, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },

    confirmImportEngine: async (engine, prompt) => {
      // Prefer the prompt the caller captured at render time; fall back to the
      // store only if it's still there. The picker menu runs its `onClose`
      // (cancelImport) before this click handler, so `get().importPrompt` has
      // usually already been nulled — relying on it alone stalls the import.
      const active = prompt ?? get().importPrompt;
      if (!active) return;
      set({ importPrompt: null, xlsxBusy: true });
      await runImport(active.sourceKey, engine, active.name);
    },

    cancelImport: () => set({ importPrompt: null }),
  };
});
