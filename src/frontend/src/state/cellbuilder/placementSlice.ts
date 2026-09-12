/**
 * Cellbuilder PLACEMENT slice — creating new boxes.
 *
 * Owns: adding a space cell / equipment / opening, seating equipment onto a
 * cell surface or at a cell-local point, extruding a new cell off a selected
 * face, and cutting an opening into one. Each names the new box, quantises it
 * to the grid, selects it, and pushes exactly one undo step.
 */

import {BOX_FACE_SIDES, extrudeBox, faceCenter, farFaceAfterExtrude, placeInCell, quantizeVec, type CellSide, type CellSurface, type Vec3} from "@/utils/cellbuilder/snap";
import type {BuilderCell} from "./types";
import type {CellBuilderSlice} from "./state";
import {nextId} from "./shared";
import {makeWithHistory} from "./historySlice";

export interface PlacementSlice {
  /** Seat equipment onto/into a cell: create a new equipment (equipmentId
   * null) or re-position an existing one on the chosen surface/side, centred
   * on the cell footprint. */
  insertEquipmentIntoCell: (opts: {
    equipmentId: string | null;
    cellId: string;
    surface: CellSurface;
    side: CellSide;
  }) => void;
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
}

export const createPlacementSlice: CellBuilderSlice<PlacementSlice> = (set) => {
  const withHistory = makeWithHistory(set);

  return {
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

  };
};
