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
import {placedCells} from "@/utils/cellbuilder/include";

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
  /** Individually hidden REFS (a plugin's load/modification/support box) — the
   * ref analogue of `hiddenCellIds`, same ephemeral (not persisted, not
   * undoable) view state. `hideCells` / `unhideCells` file an id here instead
   * of `hiddenCellIds` when it names a ref rather than a cell, so every caller
   * (keyboard shortcuts, the Properties Hide button, the visibility host) hides
   * both kinds through the one pair of verbs without knowing which it picked. */
  hiddenRefIds: string[];
  /** Toggle the port/nozzle overlay: each placed equipment's input/output
   * positions + direction vectors drawn as coloured arrows (colours match the
   * catalog editor). Off by default. */
  portsOverlayVisible: boolean;
  /** The info label (I): the selection's dimensions, masses or its provider's words, drawn
   * beside it on screen (components/viewer/cellbuilder/infoLabel.ts). Off by default. */
  infoLabel: boolean;
  setInfoLabel: (v: boolean) => void;
  /** @deprecated The cellbuilder panel's flag. Ask `isPanelOpen("core:cellbuilder")` (`@/viewer-core`); open and close it with `reveal` / `hide`. */
  panelVisible: boolean;
  /** Whether the standalone component list panel (`core:component-list`) is open. */
  componentListVisible: boolean;
  setComponentListVisible: (v: boolean) => void;
  /** Toggle whether an equipment cell renders as its type's CAD model (vs the
   * placeholder box) in the main viewer. No-op for non-equipment cells. */
  toggleCadPreview: (cellId: string) => void;
  /** @deprecated Use `reveal("core:cellbuilder")` / `hide("core:cellbuilder")` from `@/viewer-core`, which the layout controls. */
  setPanelVisible: (v: boolean) => void;
  setCellsVisible: (v: boolean) => void;
  /** Hide the given cells and/or refs (per-item "Hide selected"): each id is filed under
   *  `hiddenCellIds` or `hiddenRefIds` by which one actually names it. */
  hideCells: (ids: string[]) => void;
  /** Clear all per-cell AND per-ref hides. */
  unhideAllCells: () => void;
  /** Show the given cells and/or refs again (the inverse of `hideCells` for just these). */
  unhideCells: (ids: string[]) => void;
  /** Swap what is hidden for what is shown ("Show complement", Alt+Q / `core:show-complement`):
   *  every cell and ref not currently hidden becomes hidden, and every one that was hidden is shown.
   *  A double call is the identity. Clears the selection, as `hideCells` does when it hides the
   *  current pick. */
  showComplementCells: () => void;
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
    hiddenRefIds: [],
    portsOverlayVisible: false,
    infoLabel: false,
    setInfoLabel: (infoLabel) => set({ infoLabel }),
    panelVisible: false,
    componentListVisible: false,
    setComponentListVisible: (componentListVisible) => set({ componentListVisible }),
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
        const nextCells = new Set(s.hiddenCellIds);
        const nextRefs = new Set(s.hiddenRefIds);
        // Each id is filed under whichever it actually names — a cell or a plugin's ref — so every
        // caller can hide a mixed list without knowing which kind it picked. An id that is neither
        // (stale, already gone) is dropped silently.
        for (const id of ids) {
          if (s.cells[id]) nextCells.add(id);
          else if (s.refs[id]) nextRefs.add(id);
        }
        // A hidden cell or ref shouldn't stay selected (its box is now click-through) — drop it from
        // the multi-select set and clear the primary selection when it's hidden.
        const selHidden =
          (!!s.selection?.cellId && nextCells.has(s.selection.cellId)) ||
          (s.selection?.kind === "ref" && nextRefs.has(s.selection.refId));
        return {
          hiddenCellIds: [...nextCells],
          hiddenRefIds: [...nextRefs],
          selectedCellIds: s.selectedCellIds.filter((id) => !nextCells.has(id)),
          selectedRefIds: s.selectedRefIds.filter((id) => !nextRefs.has(id)),
          ...(selHidden
            ? { selection: null, gizmoMode: "none" as GizmoMode }
            : {}),
        };
      }),
    unhideAllCells: () => set({ hiddenCellIds: [], hiddenRefIds: [] }),
    showComplementCells: () =>
      set((s) => {
        const keepCells = new Set(s.hiddenCellIds);
        const keepRefs = new Set(s.hiddenRefIds);
        return {
          hiddenCellIds: Object.keys(s.cells).filter((id) => !keepCells.has(id)),
          hiddenRefIds: Object.keys(s.refs).filter((id) => !keepRefs.has(id)),
          selectedCellIds: [],
          selectedRefIds: [],
          selection: null,
          gizmoMode: "none" as GizmoMode,
        };
      }),
    unhideCells: (ids) =>
      set((s) => {
        const drop = new Set(ids);
        const hadCell = s.hiddenCellIds.some((id) => drop.has(id));
        const hadRef = s.hiddenRefIds.some((id) => drop.has(id));
        if (!hadCell && !hadRef) return {};
        return {
          ...(hadCell ? { hiddenCellIds: s.hiddenCellIds.filter((id) => !drop.has(id)) } : {}),
          ...(hadRef ? { hiddenRefIds: s.hiddenRefIds.filter((id) => !drop.has(id)) } : {}),
        };
      }),
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
      const topoMaxX = modelMaxX(placedCells(get().cells, get().groups, get().loftMembers));
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
