/**
 * Cellbuilder DOMAIN TYPES — the vocabulary every slice speaks.
 *
 * Owns: the shapes stored in the composed store (cells, systems, selection,
 * undo snapshots, compile-job state) and the string unions the UI switches on.
 * Owns no state and no behaviour: pure type declarations, so any slice, any
 * component and any test can import them without pulling the store in.
 */

import type {CellGroup} from "@/utils/cellbuilder/groups";
import type {LoftBand, LoftMemberDoc} from "@/utils/cellbuilder/loft";
import type {CellBox, EdgeHit} from "@/utils/cellbuilder/snap";

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
