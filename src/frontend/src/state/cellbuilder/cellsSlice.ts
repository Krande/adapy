/**
 * Cellbuilder CELL-EDITING slice.
 *
 * Owns: the `cells` map itself plus the snap grid, and every edit to an
 * EXISTING cell — move, resize by face or edge, rotate, rename, per-cell
 * params, per-instance port overrides, floor bumping, enclosure and removal.
 * Creating cells lives in the placement slice; loft band cells are regenerated
 * by the loft slice. Every mutation is wrapped in `withHistory` and marks the
 * document dirty.
 */

import {Vector3} from "three";
import {useModelState} from "@/state/modelState";
import {PORT_OVERRIDES_KEY, readPortOverrides, withPortOverride} from "@/utils/cellbuilder/ports";
import {BOX_FACE_SIDES, applyFaceOffset, quantizeVec, withAxisLength, type Vec3} from "@/utils/cellbuilder/snap";
import type {BuilderCell} from "./types";
import type {CellBuilderSlice} from "./state";
import {makeWithHistory} from "./historySlice";

export interface CellsSlice {
  cells: Record<string, BuilderCell>;
  gridStep: number;
  snapThreshold: number;
  /** Move a cell by `delta` metres along `axis` (origin-quantised, undoable) —
   * the Blender-style "G, X, 2, Enter" numeric nudge. */
  translateCellAlongAxis: (id: string, axis: 0 | 1 | 2, delta: number) => void;
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
  /** Recompute the viewer model translation from the current cells so the model
   * sits centred in the scene. Fixes a skewed placement left over after deleting
   * a far-off cell/equipment that had stretched the original bounding box. */
  recenterModel: () => void;
  setGridStep: (v: number) => void;
  setSnapThreshold: (v: number) => void;
  /** Type-derived sizing: resize every placed equipment of a given type to the
   * catalog bbox (kept centred on its footprint). Called when the equipment
   * type's bbox is edited in the admin panel. */
  resizeEquipmentOfType: (slug: string, bbox: [number, number, number]) => void;
  /** Mark a cell as a fully-enclosed room (plated walls + decks) or not, by
   * toggling its name in blueprintOptions.enclosed_cells. */
  setCellEnclosed: (cellName: string, enclosed: boolean) => void;
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
}

export const createCellsSlice: CellBuilderSlice<CellsSlice> = (set, get) => {
  const withHistory = makeWithHistory(set);

  return {
    cells: {},
    gridStep: 0.1,
    snapThreshold: 0.25,
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
    setGridStep: (gridStep) => set({ gridStep: Math.max(0, gridStep) }),
    setSnapThreshold: (snapThreshold) =>
      set({ snapThreshold: Math.max(0, snapThreshold) }),
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

  };
};
