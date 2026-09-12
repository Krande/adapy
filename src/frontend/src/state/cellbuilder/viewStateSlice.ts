/**
 * Cellbuilder VIEW-STATE slice.
 *
 * Owns: which of the three representations is shown (topology / simulation /
 * detail), the superimpose and side-by-side view modifiers, and the ephemeral,
 * non-undoable visibility state — the cell layer, per-cell hides, the port
 * overlay, per-equipment CAD preview and the panel itself. Switching
 * representation coordinates the cell layer with the two result sources and
 * compiles the target lazily the first time its view is opened.
 */

import type {GizmoMode, RepresentationMode} from "./types";
import type {CellBuilderSlice} from "./state";
import {modelMaxX} from "./bounds";

export interface ViewStateSlice {
  /** Equipment cell ids currently rendered as their type's CAD model in the
   * main viewer ("Show as CAD" per-object toggle). The 3D controller lazily
   * loads each type's preview GLB, seats it at the cell placement, and hides the
   * placeholder box for those cells; an empty list = every equipment shows its
   * box. Reset when the model unloads. A plain array so the controller's
   * reference-equality subscription fires on toggle. */
  cadPreviewCells: string[];
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
  panelVisible: boolean;
  /** Toggle whether an equipment cell renders as its type's CAD model (vs the
   * placeholder box) in the main viewer. No-op for non-equipment cells. */
  toggleCadPreview: (cellId: string) => void;
  setPanelVisible: (v: boolean) => void;
  setCellsVisible: (v: boolean) => void;
  /** Hide the given cells (per-cell "Hide selected"). */
  hideCells: (ids: string[]) => void;
  /** Clear all per-cell hides. */
  unhideAllCells: () => void;
  setPortsOverlayVisible: (v: boolean) => void;
  /** Switch the active model representation (topology / simulation / detail),
   * coordinating the cell overlay and the two result GLB sources. Compiles/loads
   * the target result lazily the first time its view is opened. */
  setRepMode: (mode: RepresentationMode) => Promise<void>;
  setSuperimpose: (on: boolean) => Promise<void>;
  /** Toggle the side-by-side result view (result offset beside the topology). */
  setSideBySide: (on: boolean) => void;
}

export const createViewStateSlice: CellBuilderSlice<ViewStateSlice> = (set, get) => {
  return {
    cadPreviewCells: [],
    repMode: "topology",
    superimpose: false,
    sideBySide: false,
    cellsVisible: true,
    hiddenCellIds: [],
    portsOverlayVisible: false,
    panelVisible: false,
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
  };
};
