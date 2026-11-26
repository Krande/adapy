/**
 * Cellbuilder PLACEMENT slice — creating new boxes.
 *
 * Owns: adding a space cell / equipment / opening, seating equipment onto a
 * cell surface or at a cell-local point, extruding a new cell off a selected
 * face, and cutting an opening into one. Each names the new box, quantises it
 * to the grid, selects it, and pushes exactly one undo step.
 *
 * A box is given in its structure's frame (relative to the structure's origin),
 * so each new one is told which structure it is in: a space by its group, an
 * equipment or opening by `STRUCTURE_NAME` -- the space it is put in's, or the
 * active structure's for one placed in the open.
 */

import {activeStructure, groupToStructureName, structureNameToGroup} from "@/utils/cellbuilder/groups";
import {hostSpaceForLocation} from "@/utils/cellbuilder/equipmentHost";
import {equipmentNameStem, nextFreeName} from "@/utils/cellbuilder/names";
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
  /** Add a box at `origin` / `size`, in the frame of `structure`: the active
   * structure when omitted, none for null. */
  addCell: (
    kind: "cell" | "equipment" | "opening",
    origin: Vec3,
    size: Vec3,
    structure?: string | null,
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
  /** The Location select (FLOOR/ROOF) -- sets SPACE_LOC and keeps the equipment's X/Y
   * and its OFFSET above its seating surface (so it does not jump): `z = newSurface + (oldZ -
   * oldSurface)`, surface = the host space's Z for FLOOR, Z + DZ for ROOF -- matching
   * `placeInCell`'s "top" side and the compiler's own ROOF rule. The host space is
   * `params.SPACE_NAME` if it names a real space (in the equipment's own structure), else, among
   * the spaces whose X/Y footprint actually contains the equipment (stacked spaces at the same
   * X/Y but different Z are common, e.g. a mezzanine deck under a roof deck), the one whose
   * CURRENT-mode surface (its floor while SPACE_LOC is FLOOR, its roof while ROOF) is nearest the
   * equipment's current Z; without one, only SPACE_LOC changes, as before this existed (a
   * freshly-placed equipment with no space is exceedingly rare, but not impossible) -- never the
   * first space anywhere, which put the equipment on the wrong floor whenever that space was not
   * the right one. One undo step. `insertEquipmentIntoCell` is NOT reused here: it re-centres X/Y
   * in the space, which would throw away the user's own placement. */
  setEquipmentLocation: (id: string, loc: "FLOOR" | "ROOF") => void;
}

/** `params` naming `structure` as the item's STRUCTURE_NAME (dropped for none). */
function inStructure(params: Record<string, unknown>, structure: string | undefined): Record<string, unknown> {
  const next = { ...params };
  const name = groupToStructureName(structure);
  if (name) next.STRUCTURE_NAME = name;
  else delete next.STRUCTURE_NAME;
  return next;
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
                // The cell the popover seated it on is its SPACE_NAME: it must stick, not be
                // re-derived later from whichever space's footprint happens to hold its X/Y
                // centre (containingCellName falls back to that only when unset).
                params: inStructure({ ...eq.params, SPACE_NAME: cell.name, SPACE_LOC: spaceLoc }, cell.group),
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
        const eqType = s.selectedEquipmentType ?? undefined;
        const name = nextFreeName(equipmentNameStem(eqType), s.cells);
        const size: Vec3 = [1, 1, 1];
        const eqCell: BuilderCell = {
          id,
          name,
          kind: "equipment",
          equipmentType: eqType,
          origin: placeInCell(cell, size, surface, side, step),
          size,
          params: inStructure({ SPACE_NAME: cell.name, SPACE_LOC: spaceLoc }, cell.group),
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
        const eqType = s.selectedEquipmentType ?? undefined;
        const name = nextFreeName(equipmentNameStem(eqType), s.cells);
        const size: Vec3 = [1, 1, 1];
        // Cell-local (X,Y) -> world origin (the cells map stores world coords);
        // seat on the cell floor (origin z = cell floor).
        const origin = quantizeVec(
          [cell.origin[0] + local[0], cell.origin[1] + local[1], cell.origin[2]],
          s.gridStep,
        );
        const eqCell: BuilderCell = {
          id,
          name,
          kind: "equipment",
          equipmentType: eqType,
          origin,
          size,
          params: inStructure({ SPACE_NAME: cell.name, SPACE_LOC: "FLOOR" }, cell.group),
        };
        return {
          cells: { ...s.cells, [id]: eqCell },
          dirty: true,
          mode: "idle",
          selection: { kind: "cell", cellId: id },
          selectedCellIds: [id],
          selectedRefIds: [],
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
        // Inherit the active cell type's entity metadata, exactly like addCell.
        const cellType = s.cellTypes.find((t) => t.slug === s.selectedCellType);
        const cell: BuilderCell = {
          id,
          name: nextFreeName("Space", s.cells),
          kind: "cell",
          origin: quantizeVec(box.origin, s.gridStep),
          size: quantizeVec(box.size, s.gridStep),
          params: cellType?.metadata ? { ...cellType.metadata } : {},
          // Grown off a face of `cur`, so in its frame: in its structure.
          ...(cur.group ? { group: cur.group } : {}),
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
          selectedRefIds: [],
        };
      }),
    addCell: (kind, origin, size, structure) =>
      withHistory((s) => {
        const id = nextId();
        const into =
          structure === undefined
            ? activeStructure(s)
            : structure !== null && s.groups.some((g) => g.name === structure)
              ? structure
              : undefined;
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
        const nameStem =
          kind === "cell" ? "Space" : kind === "opening" ? "OPENING" : equipmentNameStem(eqType);
        const cell: BuilderCell = {
          id,
          name: nextFreeName(nameStem, s.cells),
          kind,
          equipmentType: eqType,
          subtype,
          origin: quantizeVec(origin, s.gridStep),
          size: quantizeVec(size, s.gridStep),
          // A cell type may carry extra TopoSpace entity fields (round-tripped
          // verbatim); openings/equipment start with none but their structure.
          // A new space goes into the structure (by default the active one); an
          // equipment or opening names it as its STRUCTURE_NAME: its box is in
          // that structure's frame.
          ...(kind === "cell"
            ? { params: cellType?.metadata ? { ...cellType.metadata } : {}, ...(into ? { group: into } : {}) }
            : { params: inStructure({}, into) }),
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
        // On the grid in the face's plane, at least one step wide; THROUGH the face it keeps its
        // thickness and straddles the face plane exactly. Snapped there too, a coarse grid (a 1.3 m
        // one) rounded the 0.3 m to nothing: an opening lying in the face plane, which the engine
        // could not cut (OCCT Standard_DomainError).
        const q = quantizeVec(origin, s.gridStep);
        const qs = quantizeVec(size, s.gridStep);
        for (const a of [0, 1, 2] as const) {
          if (a === side.axis) {
            q[a] = origin[a];
            qs[a] = size[a];
          } else if (!(qs[a] > 0)) {
            qs[a] = s.gridStep > 0 ? s.gridStep : size[a];
          }
        }
        const id = nextId();
        const opening: BuilderCell = {
          id,
          name: nextFreeName("OPENING", s.cells),
          kind: "opening",
          subtype,
          origin: q,
          size: qs,
          // SPACE_NAME/SPACE_SIDE: metadata only (the opening stays USE_GLOBAL_COORDS, written as
          // X/Y/Z/DX/DY/DZ -- the compiled geometry is unchanged), but without it the tree files
          // this by geometry alone (containingCell), which an opening straddling a wall often
          // loses to float noise or the adjacent space, landing it as "unassigned".
          params: inStructure({ SPACE_NAME: cell.name, SPACE_SIDE: side.label.replace("+", "") }, cell.group),
        };
        return {
          cells: { ...s.cells, [id]: opening },
          dirty: true,
          mode: "idle",
          selection: { kind: "cell", cellId: id },
        };
      }),
    setEquipmentLocation: (id, loc) =>
      withHistory((s) => {
        const cur = s.cells[id];
        if (!cur || cur.kind !== "equipment") return {};
        const storedLoc = cur.params.SPACE_LOC as "FLOOR" | "ROOF" | undefined;
        const oldLoc = storedLoc ?? "FLOOR";
        // An unset SPACE_LOC already reads (and shows) as FLOOR, but picking FLOOR again must
        // still WRITE it -- not stop here just because the effective value didn't change -- or
        // the doc keeps no SPACE_LOC and the write default (toDoc) is the only thing deciding it.
        // Only an already-EXPLICIT value equal to the pick is a true no-op.
        if (storedLoc === loc) return {};
        // A named SPACE_NAME wins outright, scoped to the equipment's own structure (even if
        // stale -- the "no host" fallback below then applies, as before); otherwise pick among
        // the spaces actually under/over the equipment (hostSpaceForLocation), never just the
        // first space anywhere.
        const structure = structureNameToGroup(cur.params ?? {});
        const space = typeof cur.params.SPACE_NAME === "string" && cur.params.SPACE_NAME
          ? Object.values(s.cells).find(
              (c) => c.kind === "cell" && c.name === cur.params.SPACE_NAME && (structure === undefined || c.group === structure),
            ) ?? null
          : hostSpaceForLocation(s.cells, cur, oldLoc);
        // No host space: only SPACE_LOC changes, as before this action existed.
        const origin = space
          ? (() => {
              const surfaceFor = (l: "FLOOR" | "ROOF") => (l === "ROOF" ? space.origin[2] + space.size[2] : space.origin[2]);
              const z = surfaceFor(loc) + (cur.origin[2] - surfaceFor(oldLoc));
              return [cur.origin[0], cur.origin[1], z] as Vec3;
            })()
          : cur.origin;
        return {
          cells: {
            ...s.cells,
            [id]: { ...cur, origin, params: { ...cur.params, SPACE_LOC: loc } },
          },
          dirty: true,
        };
      }),

  };
};
