import { create } from "zustand";

import {
  ApiError,
  viewerApi,
  type ProceduralDoc,
  type ProceduralDesignRulesetOption,
  type ProceduralSystemTypeOption,
  type ProceduralTypeOption,
} from "@/services/viewerApi";
import {
  useConversionStore,
  type ConversionJob,
} from "@/state/conversionStore";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { pushSnapshot, redoStep, undoStep } from "@/utils/cellbuilder/history";
import {
  applyFaceOffset,
  BOX_FACE_SIDES,
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
export interface BuilderCell extends CellBox {
  id: string;
  name: string;
  kind: "cell" | "equipment";
  /** Archetype name (pump/tank/...) for equipment cells; from the
   * worker-advertised list. */
  equipmentType?: string;
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
  | "drag-face";

/** Active direct-manipulation gizmo for the selected cell. */
export type GizmoMode = "none" | "translate" | "resize";

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
  systems: Record<string, BuilderSystem>;
  blueprintOptions: Record<string, unknown>;
  equipmentCad: boolean;
  designRules: string;
}

const HISTORY_LIMIT = 100;

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
const SPACE_OWN_KEYS = new Set(["NAME", "X", "Y", "Z", "DX", "DY", "DZ"]);
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
  /** Which direct-manipulation gizmo is active for the selected cell: none, a
   * translate widget, or the face-handle resize gizmo. Reset to "none" whenever
   * the selected cell changes. */
  gizmoMode: GizmoMode;
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
  /** System types for the systems inspector: code kinds ∪ DB templates. */
  systemTypes: ProceduralSystemTypeOption[];
  compileJob: CompileJobState | null;
  /** Source name of the compiled result currently loaded in the scene. */
  resultSourceName: string | null;
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
  setGizmoMode: (mode: GizmoMode) => void;
  setFaceDragResize: (v: boolean) => void;
  openContextMenu: (x: number, y: number, cellId: string) => void;
  closeContextMenu: () => void;
  openInsertMenu: (x: number, y: number, equipmentId: string | null) => void;
  closeInsertMenu: () => void;
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
  setCellsVisible: (v: boolean) => void;
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
  setSelectedEquipmentType: (t: string | null) => void;
  /** Type-derived sizing: resize every placed equipment of a given type to the
   * catalog bbox (kept centred on its footprint). Called when the equipment
   * type's bbox is edited in the admin panel. */
  resizeEquipmentOfType: (slug: string, bbox: [number, number, number]) => void;
  /** Mark a cell as a fully-enclosed room (plated walls + decks) or not, by
   * toggling its name in blueprintOptions.enclosed_cells. */
  setCellEnclosed: (cellName: string, enclosed: boolean) => void;
  addCell: (kind: "cell" | "equipment", origin: Vec3, size: Vec3) => void;
  updateCell: (id: string, patch: Partial<BuilderCell>) => void;
  /** Rename a cell/equipment; for equipment, rewrites matching system
   * connections so no run is orphaned. No-op on an empty/duplicate name. */
  renameCell: (id: string, name: string) => void;
  setCellParam: (id: string, key: string, value: unknown) => void;
  /** Extend (positive) / contract (negative) a face outward by `length`. */
  applyFaceExtension: (id: string, faceIndex: number, length: number) => void;
  /** Set the box length along `axis` (origin fixed). */
  setEdgeLength: (id: string, axis: 0 | 1 | 2, length: number) => void;
  removeCell: (id: string) => void;
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
  /** Populate the model with the topo_model demo layout (2 cells, deck +
   * interior pump/tank pairs, reinforced internal wall). */
  loadDemoTemplate: () => void;
  /** Restore the previous / next model snapshot. */
  undo: () => void;
  redo: () => void;
  /** Coalesce a burst of mutations (e.g. a face drag) into one undo step. */
  beginTransaction: () => void;
  endTransaction: () => void;
  fetchEquipmentTypes: () => Promise<void>;
  fetchSystemTypes: () => Promise<void>;
  fetchDesignRulesets: () => Promise<void>;
  /** Persist a code-origin type into the scope's DB catalog, then refresh. */
  syncEquipmentTypeToDb: (slug: string) => Promise<void>;
  syncSystemTypeToDb: (slug: string) => Promise<void>;
  commit: () => Promise<boolean>;
  /** Compile the active model. ``force`` recompiles even if the revision's GLB
   * is already cached — used when the compiler engine changed but the document
   * (the cache key) did not, so a plain Compile would return the stale blob. */
  compile: (force?: boolean) => Promise<void>;
  viewResult: (derivedKey: string) => Promise<void>;
  hideResult: () => void;
}

function cellsFromDoc(doc: ProceduralDoc): Record<string, BuilderCell> {
  const out: Record<string, BuilderCell> = {};
  for (const s of doc.spaces ?? []) {
    const id = nextId();
    out[id] = {
      id,
      name: String(s.NAME ?? id),
      kind: "cell",
      origin: [Number(s.X ?? 0), Number(s.Y ?? 0), Number(s.Z ?? 0)],
      size: [Number(s.DX ?? 1), Number(s.DY ?? 1), Number(s.DZ ?? 1)],
      params: extractParams(s, SPACE_OWN_KEYS),
    };
  }
  for (const e of doc.equipments ?? []) {
    const id = nextId();
    out[id] = {
      id,
      name: String(e.NAME ?? id),
      kind: "equipment",
      equipmentType:
        typeof e.DESCRIPTION === "string" && e.DESCRIPTION
          ? e.DESCRIPTION
          : undefined,
      origin: [Number(e.X ?? 0), Number(e.Y ?? 0), Number(e.Z ?? 0)],
      size: [Number(e.LX ?? 1), Number(e.LY ?? 1), Number(e.LZ ?? 1)],
      params: extractParams(e, EQUIPMENT_OWN_KEYS),
    };
  }
  return out;
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

function snapshot(s: CellBuilderState): ModelSnapshot {
  return {
    cells: s.cells,
    systems: s.systems,
    blueprintOptions: s.blueprintOptions,
    equipmentCad: s.equipmentCad,
    designRules: s.designRules,
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

  return {
    active: null,
    cells: {},
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
    gizmoMode: "none",
    faceDragResize: false,
    contextMenu: null,
    insertMenu: null,
    gridStep: 0.1,
    snapThreshold: 0.25,
    dirty: false,
    autoCompile: true,
    committing: false,
    conflict: null,
    equipmentTypes: [],
    selectedEquipmentType: null,
    systemTypes: [],
    compileJob: null,
    resultSourceName: null,
    cellsVisible: true,
    hiddenCellIds: [],
    portsOverlayVisible: false,
    blueprintOptions: {},
    equipmentCad: false,
    designRules: "standard",
    designRulesets: [],
    panelVisible: false,

    open: (modelId, name, revision, doc) => {
      // A freshly loaded model starts a new editing session — history resets.
      set({
        active: { modelId, name, revision },
        cells: cellsFromDoc(doc),
        systems: systemsFromDoc(doc),
        blueprintOptions: doc.blueprint ?? {},
        equipmentCad: Boolean(doc.equipment_cad),
        designRules: doc.design_rules ?? "standard",
        past: [],
        future: [],
        txDepth: 0,
        mode: "idle",
        selection: null,
        selectedCellIds: [],
        gizmoMode: "none",
        contextMenu: null,
        insertMenu: null,
        dirty: false,
        conflict: null,
        compileJob: null,
        panelVisible: true,
        hiddenCellIds: [],
      });
      void get().fetchEquipmentTypes();
      void get().fetchSystemTypes();
      void get().fetchDesignRulesets();
    },
    close: () => {
      get().hideResult();
      set({
        active: null,
        cells: {},
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
        dirty: false,
        panelVisible: false,
        compileJob: null,
        hiddenCellIds: [],
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
    setGizmoMode: (gizmoMode) => set({ gizmoMode }),
    setFaceDragResize: (faceDragResize) => set({ faceDragResize }),
    openContextMenu: (x, y, cellId) => set({ contextMenu: { x, y, cellId } }),
    closeContextMenu: () => set({ contextMenu: null }),
    openInsertMenu: (x, y, equipmentId) =>
      set({ insertMenu: { x, y, equipmentId }, contextMenu: null }),
    closeInsertMenu: () => set({ insertMenu: null }),
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
    setCellsVisible: (cellsVisible) => set({ cellsVisible }),
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
        const baseName =
          kind === "cell" ? "CELL" : (eqType ?? "EQ").toUpperCase();
        const cell: BuilderCell = {
          id,
          name: `${baseName}_${String(count).padStart(2, "0")}`,
          kind,
          equipmentType: eqType,
          origin: quantizeVec(origin, s.gridStep),
          size: quantizeVec(size, s.gridStep),
          params: {},
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
    updateCell: (id, patch) =>
      withHistory((s) => {
        const cur = s.cells[id];
        if (!cur) return {};
        return {
          cells: { ...s.cells, [id]: { ...cur, ...patch } },
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
        .map((c) => ({
          INCLUDE: true,
          ...c.params,
          NAME: c.name,
          X: c.origin[0],
          Y: c.origin[1],
          Z: c.origin[2],
          DX: c.size[0],
          DY: c.size[1],
          DZ: c.size[2],
        }));
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
      return {
        grid: {},
        blueprint: get().blueprintOptions,
        design_rules: get().designRules,
        equipment_cad: get().equipmentCad,
        spaces,
        equipments,
        systems,
        openings: [],
      };
    },
    loadFromDoc: (doc) =>
      set({
        cells: cellsFromDoc(doc),
        systems: systemsFromDoc(doc),
        blueprintOptions: doc.blueprint ?? {},
        equipmentCad: Boolean(doc.equipment_cad),
        designRules: doc.design_rules ?? "standard",
        past: [],
        future: [],
        txDepth: 0,
        dirty: false,
        selection: null,
      }),

    loadDemoTemplate: () => {
      // A two-storey structure with fully-enclosed rooms (reinforced internal +
      // external walls, plus the reinforced floor/roof decks), a range of routed
      // services (piping / electrical / duct) and site I/O so every run is
      // two-ended. Coordinates are min-corners (equipment archetypes centre on
      // the footprint). Ground floor z 0..3, second floor z 3..6, roof at z 6.
      const doc: ProceduralDoc = {
        grid: {},
        // Only Cell3 (the HVAC room) is fully enclosed — plated walls + decks;
        // the other cells stay open steel frame.
        blueprint: {
          enclosed_cells: ["Cell3"],
        },
        design_rules: "standard",
        spaces: [
          {
            NAME: "Cell1",
            INCLUDE: true,
            X: 0,
            Y: 0,
            Z: 0,
            DX: 5,
            DY: 5,
            DZ: 3,
          },
          {
            NAME: "Cell2",
            INCLUDE: true,
            X: 5,
            Y: 0,
            Z: 0,
            DX: 5,
            DY: 5,
            DZ: 3,
          },
          {
            NAME: "Cell3",
            INCLUDE: true,
            X: 0,
            Y: 0,
            Z: 3,
            DX: 5,
            DY: 5,
            DZ: 3,
          },
          {
            NAME: "Cell4",
            INCLUDE: true,
            X: 5,
            Y: 0,
            Z: 3,
            DX: 5,
            DY: 5,
            DZ: 3,
          },
        ],
        equipments: [
          // Ground floor (Cell1/Cell2)
          {
            NAME: "Pump2",
            DESCRIPTION: "pump",
            X: 2,
            Y: 2,
            Z: 0,
            LX: 1,
            LY: 1,
            LZ: 1,
          },
          {
            NAME: "Tank2",
            DESCRIPTION: "tank",
            X: 6.5,
            Y: 1.5,
            Z: 0,
            LX: 2,
            LY: 2,
            LZ: 2,
          },
          {
            NAME: "SB2",
            DESCRIPTION: "switchboard",
            X: 0.3,
            Y: 2,
            Z: 0,
            LX: 0.8,
            LY: 0.4,
            LZ: 1.2,
          },
          // Second floor (Cell3/Cell4) — Cell3 is the HVAC room
          {
            NAME: "Pump1",
            DESCRIPTION: "pump",
            X: 2,
            Y: 2,
            Z: 3,
            LX: 1,
            LY: 1,
            LZ: 1,
          },
          {
            NAME: "Tank1",
            DESCRIPTION: "tank",
            X: 6.5,
            Y: 1.5,
            Z: 3,
            LX: 2,
            LY: 2,
            LZ: 2,
          },
          {
            NAME: "SB1",
            DESCRIPTION: "switchboard",
            X: 0.3,
            Y: 2,
            Z: 3,
            LX: 0.8,
            LY: 0.4,
            LZ: 1.2,
          },
          {
            NAME: "HVAC1",
            DESCRIPTION: "hvac",
            X: 3,
            Y: 3.5,
            Z: 3,
            LX: 1.5,
            LY: 1,
            LZ: 1.2,
          },
          // Roof — the duct exhausts up to this unit on top of the structure
          {
            NAME: "Exhaust1",
            DESCRIPTION: "exhaust_fan",
            X: 3,
            Y: 3.5,
            Z: 6,
            LX: 0.8,
            LY: 0.8,
            LZ: 0.6,
          },
        ],
        systems: [
          // Process piping
          {
            NAME: "CoolingWater",
            TYPE: "piping",
            MEDIUM: "water",
            CONNECTIONS: [
              { EQUIPMENT: "Pump1", PORT: "discharge" },
              { EQUIPMENT: "Tank1", PORT: "inlet" },
            ],
          },
          {
            NAME: "ServiceWater",
            TYPE: "piping",
            MEDIUM: "water",
            CONNECTIONS: [
              { EQUIPMENT: "Pump2", PORT: "discharge" },
              { EQUIPMENT: "Tank2", PORT: "inlet" },
            ],
          },
          // Electrical distribution: mains enters at the Cell1 edge into the
          // ground switchboard (SB2), which feeds the local pump AND a second
          // switchboard (SB1) up in Cell3; SB1 in turn feeds its room's loads.
          {
            NAME: "Mains",
            TYPE: "electrical",
            CONNECTIONS: [
              {
                SITE: "grid_supply",
                POSITION: [0, 1, 1],
                DIRECTION: "IN",
                DIRECTION_VECTOR: [1, 0, 0],
              },
              { EQUIPMENT: "SB2", PORT: "incoming" },
            ],
          },
          {
            NAME: "PowerFeed2",
            TYPE: "electrical",
            CONNECTIONS: [
              { EQUIPMENT: "SB2", PORT: "feeder" },
              { EQUIPMENT: "Pump2", PORT: "power" },
            ],
          },
          {
            NAME: "DeckTie",
            TYPE: "electrical",
            CONNECTIONS: [
              { EQUIPMENT: "SB2", PORT: "feeder2" },
              { EQUIPMENT: "SB1", PORT: "incoming" },
            ],
          },
          {
            NAME: "PowerFeed1",
            TYPE: "electrical",
            CONNECTIONS: [
              { EQUIPMENT: "SB1", PORT: "feeder" },
              { EQUIPMENT: "Pump1", PORT: "power" },
            ],
          },
          {
            NAME: "HvacPower",
            TYPE: "electrical",
            CONNECTIONS: [
              { EQUIPMENT: "SB1", PORT: "feeder2" },
              { EQUIPMENT: "HVAC1", PORT: "power" },
            ],
          },
          // HVAC duct: the room's air handler exhausts up to the roof fan
          {
            NAME: "HvacExhaust",
            TYPE: "duct",
            MEDIUM: "air",
            CONNECTIONS: [
              { EQUIPMENT: "HVAC1", PORT: "supply" },
              { EQUIPMENT: "Exhaust1", PORT: "intake" },
            ],
          },
          // Remaining site I/O — all at the Cell1 edge (x=0)
          {
            NAME: "Drain",
            TYPE: "piping",
            MEDIUM: "water",
            CONNECTIONS: [
              { EQUIPMENT: "Tank2", PORT: "outlet" },
              {
                SITE: "drain",
                POSITION: [0, 2.5, 1],
                DIRECTION: "OUT",
                DIRECTION_VECTOR: [1, 0, 0],
              },
            ],
          },
          {
            NAME: "Suction",
            TYPE: "piping",
            MEDIUM: "water",
            CONNECTIONS: [
              {
                SITE: "seawater",
                POSITION: [0, 4, 1],
                DIRECTION: "IN",
                DIRECTION_VECTOR: [1, 0, 0],
              },
              { EQUIPMENT: "Pump2", PORT: "suction" },
            ],
          },
        ],
        openings: [],
      };
      // Undoable: pushes the pre-template state so the user can back out.
      withHistory(() => ({
        cells: cellsFromDoc(doc),
        systems: systemsFromDoc(doc),
        blueprintOptions: doc.blueprint ?? {},
        designRules: doc.design_rules ?? "standard",
        dirty: true,
        selection: null,
        mode: "idle",
      }));
    },

    undo: () =>
      set((s) => {
        const step = undoStep(s, snapshot(s), HISTORY_LIMIT);
        if (!step) return {};
        return {
          ...step.restored,
          ...step.stacks,
          dirty: true,
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
          dirty: true,
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

    compile: async (force = false) => {
      const s = get();
      if (!s.active) return;
      if (s.dirty) {
        const ok = await get().commit();
        // commit() auto-compiles on success when enabled; avoid double-run.
        // A forced recompile still proceeds — the auto-compile after commit is a
        // normal (cache-honouring) run, so we fall through to re-run with force.
        if (ok && get().autoCompile && !force) return;
        if (!ok) return;
      }
      const active = get().active;
      if (!active) return;
      const label = active.name;
      // Announce the task on the global toast panel right away (before the
      // enqueue round-trip resolves), then keep the same toast updated through
      // the poll below — mirrors how conversion/FEA feed conversionStore.
      setProceduralToast(label, {
        status: "queued",
        stage: "queued",
        progress: 0,
        startedAt: Date.now(),
      });
      // Auto-show the compiled result once ready when auto-compile is on, so
      // Commit -> compile -> render is one gesture.
      const autoShow = () => {
        const cur = get().compileJob;
        if (!cur || !cur.derivedKey) return;
        // Show the result when auto-compile is on, OR refresh a result that is
        // already on screen — a forced recompile overwrites the same derivedKey
        // with new bytes (the loader re-fetches via a fresh presigned URL), so the
        // displayed model must reload to reflect the rebuild.
        if (get().autoCompile || get().resultSourceName !== null)
          void get().viewResult(cur.derivedKey);
      };
      try {
        const res = await viewerApi.compileProceduralModel(
          currentScopePart(),
          active.modelId,
          force,
        );
        if (res.cached) {
          set({
            compileJob: {
              jobId: null,
              derivedKey: res.derived_key,
              status: "cached",
            },
          });
          setProceduralToast(label, {
            status: "done",
            progress: 1,
            stage: "cached",
            derivedKey: res.derived_key,
          });
          autoShow();
          return;
        }
        set({
          compileJob: {
            jobId: res.job_id,
            derivedKey: res.derived_key,
            status: "queued",
          },
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
              return;
            }
            if (st.status === "error") {
              set({
                compileJob: {
                  ...cur,
                  status: "error",
                  error: st.error ?? "compile failed",
                },
              });
              setProceduralToast(label, {
                status: "error",
                stage: st.stage || "",
                error: st.error ?? "compile failed",
              });
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
        set({
          compileJob: {
            jobId: null,
            derivedKey: "",
            status: "error",
            error,
          },
        });
        setProceduralToast(label, { status: "error", error });
      }
    },

    viewResult: async (derivedKey: string) => {
      const active = get().active;
      const sourceName = `procedural:${active ? active.name : derivedKey}`;
      const { load_glb_by_url_rest } = await import(
        "@/utils/scene/handlers/view_file_object_from_server"
      );
      await load_glb_by_url_rest(currentScopePart(), derivedKey, sourceName);
      set({ resultSourceName: sourceName });
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
  };
});
