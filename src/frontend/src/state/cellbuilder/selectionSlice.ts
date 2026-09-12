/**
 * Cellbuilder SELECTION, TOOL-MODE and MENU slice.
 *
 * Owns: what is picked (cell / face / edge, plus the multi-select set), the
 * builder's tool mode and select mode, the gizmo mode + axis lock + snap
 * toggles, the three screen-positioned popovers (context / insert / port), the
 * port-edit gizmo, and the keyboard cycling that walks selection granularity,
 * faces, edges and cells. Pure UI state — nothing here is undoable.
 */

import {cycleFaceIndex, edgeIndexInFace, faceEdges} from "@/utils/cellbuilder/snap";
import type {BuilderSelection, CellBuilderMode, GizmoMode, SelectMode} from "./types";
import type {CellBuilderSlice} from "./state";

export interface SelectionSlice {
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
  /** Cycle the selection's granularity cell -> face -> edge (keyboard Tab),
   * re-deriving the current pick at the new level on the same cell. */
  cycleSelectMode: (dir: 1 | -1) => void;
  /** Cycle the active element (keyboard F/D): faces of a box cell, edges of a
   * face in edge mode, or bays/stations of a loft member. */
  cycleSelectionElement: (dir: 1 | -1) => void;
  /** Select the next/previous cell by name order (keyboard N/P). */
  selectAdjacentCell: (dir: 1 | -1) => void;
}

export const createSelectionSlice: CellBuilderSlice<SelectionSlice> = (set, get) => {
  return {
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
    focusedSystemName: null,
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

  };
};
