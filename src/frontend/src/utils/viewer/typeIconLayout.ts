import type {ProceduralTypeOption} from "@/services/api/proceduralCatalog";
import type {BuilderCell, BuilderSystem} from "@/state/cellbuilder/types";
import type {Point3} from "@/viewport/api/billboards";
import {
  classifyEquipment,
  classifyMedium,
  missingInputs,
  type EquipIcon,
  type MediumMarker,
} from "@/utils/viewer/typeIconClassify";

// Where the type-icon overlay puts its icons, as plain data: which icon, at which world position and
// how large, and what it stands for. Whichever viewport is on screen draws them.

/** One icon of the overlay. */
export type TypeIcon = {position: Point3; size: number} & (
  | {kind: "equipment"; icon: EquipIcon; equipment: string}
  | {kind: "missing"; equipment: string; missing: string[]}
  | {kind: "medium"; icon: MediumMarker; system: string}
);

export interface TypeIconInput {
  cells: Record<string, BuilderCell>;
  systems: Record<string, BuilderSystem>;
  equipmentTypes: readonly ProceduralTypeOption[];
  show: {equipment: boolean; media: boolean; missing: boolean};
  /** An icon's world size (`typeIconScale`). */
  scale: number;
  /** Where the model's own coordinates sit in the world (the cells are in the model's). */
  translation: Point3 | null;
  /** The middle of a system's routed pipe in the world, or null while the compiled model has none. */
  routeCentre: (system: string) => Point3 | null;
}

/** Icon world size relative to the model (falls back to the cells' extent when
 * no GLB is loaded yet — the cellbuilder case). `bounds` is the model's box,
 * min xyz then max xyz. */
export function typeIconScale(
  bounds: readonly number[] | null,
  cells: Record<string, BuilderCell>,
): number {
  if (bounds)
    return Math.hypot(bounds[3] - bounds[0], bounds[4] - bounds[1], bounds[5] - bounds[2]) * 0.03;
  const maxDim = Math.max(1, ...Object.values(cells).map((c) => Math.max(...c.size)));
  return maxDim * 0.6;
}

export function layoutTypeIcons(input: TypeIconInput): TypeIcon[] {
  const {show, scale: s} = input;
  const [tx, ty, tz] = input.translation ?? [0, 0, 0];
  const out: TypeIcon[] = [];
  const off = s * 0.75;
  const bySlug = new Map(input.equipmentTypes.map((o) => [o.slug, o]));
  const equipmentCells = Object.values(input.cells).filter(
    (c) => c.kind === "equipment",
  );

  // every (equipment, port) pair a system connects to (site terminals have no
  // equipment/port and are skipped)
  const connected = new Set<string>();
  for (const sys of Object.values(input.systems))
    for (const c of sys.connections)
      if (c.equipment && c.port) connected.add(`${c.equipment}::${c.port}`);

  const centerOf = (cell: BuilderCell) => ({
    x: cell.origin[0] + cell.size[0] / 2,
    y: cell.origin[1] + cell.size[1] / 2,
    top: cell.origin[2] + cell.size[2],
  });

  for (const cell of equipmentCells) {
    const opt = cell.equipmentType ? bySlug.get(cell.equipmentType) : undefined;
    const ports = opt?.ports;
    const c = centerOf(cell);
    if (show.equipment) {
      out.push({
        kind: "equipment",
        icon: classifyEquipment(cell.equipmentType, ports),
        equipment: cell.name,
        position: [c.x + tx, c.y + ty, c.top + off + tz],
        size: s,
      });
    }
    if (show.missing) {
      const missing = missingInputs(cell.name, ports, connected);
      if (missing.length > 0) {
        out.push({
          kind: "missing",
          equipment: cell.name,
          missing,
          position: [c.x + s * 0.55 + tx, c.y + ty, c.top + off + s * 0.85 + tz],
          size: s * 0.8,
        });
      }
    }
  }

  if (show.media) {
    const byName = new Map(equipmentCells.map((c) => [c.name, c]));
    for (const sys of Object.values(input.systems)) {
      // Prefer the real routed pipe: hug the compiled GLB geometry so the
      // marker sits over the actual pipe, not the system bounds. Falls back to
      // the connected-equipment centroid before the model is compiled, or when
      // no matching run is in the scene.
      const world = input.routeCentre(sys.name);
      let position: Point3;
      if (world) {
        // The centroid sits on the pipe centerline; lift by a small fraction
        // of the icon size so the marker rests just above the pipe surface
        // rather than floating well over it.
        position = [world[0], world[1], world[2] + off * 0.4];
      } else {
        const pts = sys.connections
          .map((c) => (c.equipment ? byName.get(c.equipment) : undefined))
          .filter((c): c is BuilderCell => !!c)
          .map(centerOf);
        if (pts.length === 0) continue;
        position = [
          pts.reduce((a, p) => a + p.x, 0) / pts.length + tx,
          pts.reduce((a, p) => a + p.y, 0) / pts.length + ty,
          Math.max(...pts.map((p) => p.top)) + off * 1.6 + tz,
        ];
      }
      out.push({
        kind: "medium",
        icon: classifyMedium(sys.type, sys.medium),
        system: sys.name,
        position,
        size: s * 0.85,
      });
    }
  }
  return out;
}
