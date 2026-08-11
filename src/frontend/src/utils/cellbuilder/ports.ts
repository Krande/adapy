// Pure helpers for equipment-port editing in the cellbuilder.
//
// Ports come from the equipment TYPE (the per-scope catalog / code archetype),
// so every placed instance of a type shares the same nominal port geometry. A
// per-instance edit (drag a port arrow in the 3D preview) is stored as an
// OVERRIDE on the equipment cell — a map keyed by port name — that round-trips
// verbatim through the doc (BuilderCell.params.PORT_OVERRIDES, mirroring how a
// rotation round-trips as ROT_X/Y/Z) so it survives a recompile. The overlay
// merges the type ports with these overrides via `effectivePorts`.

import type { TypePortSummary } from "@/services/viewerApi";
import { boxCorners, type Vec3 } from "@/utils/cellbuilder/snap";

/** A per-port, per-instance override — only the geometry fields a user can
 * edit in the preview (position + outward direction). Absent fields fall back
 * to the type's port geometry. */
export interface PortOverride {
  position?: [number, number, number];
  direction_vector?: [number, number, number];
}

/** Overrides for one equipment instance, keyed by port name. */
export type PortOverrides = Record<string, PortOverride>;

/** The key under which port overrides ride in an equipment cell's `params`
 * (so they round-trip verbatim through toDoc → the equipment entity). */
export const PORT_OVERRIDES_KEY = "PORT_OVERRIDES";

/** Parse the port overrides carried on an equipment cell's params (verbatim
 * from a loaded doc), dropping anything malformed. Never throws. */
export function readPortOverrides(
  params: Record<string, unknown> | undefined | null,
): PortOverrides {
  const raw = params?.[PORT_OVERRIDES_KEY];
  if (!raw || typeof raw !== "object") return {};
  const out: PortOverrides = {};
  for (const [name, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!v || typeof v !== "object") continue;
    const o = v as Record<string, unknown>;
    const ov: PortOverride = {};
    if (Array.isArray(o.position) && o.position.length === 3)
      ov.position = [Number(o.position[0]), Number(o.position[1]), Number(o.position[2])];
    if (Array.isArray(o.direction_vector) && o.direction_vector.length === 3)
      ov.direction_vector = [
        Number(o.direction_vector[0]),
        Number(o.direction_vector[1]),
        Number(o.direction_vector[2]),
      ];
    if (ov.position || ov.direction_vector) out[name] = ov;
  }
  return out;
}

/** Merge one port edit into an overrides map immutably, returning the new map.
 * A patch only carries the field(s) that changed (position OR direction). */
export function withPortOverride(
  overrides: PortOverrides,
  name: string,
  patch: PortOverride,
): PortOverrides {
  const prev = overrides[name] ?? {};
  return { ...overrides, [name]: { ...prev, ...patch } };
}

/** Type ports with per-instance overrides applied by name (position/direction
 * only). Ports without an override pass through unchanged. */
export function effectivePorts(
  typePorts: TypePortSummary[],
  overrides: PortOverrides,
): TypePortSummary[] {
  if (!typePorts.length) return typePorts;
  return typePorts.map((p) => {
    const ov = overrides[p.name];
    if (!ov) return p;
    return {
      ...p,
      position: ov.position ?? p.position,
      direction_vector: ov.direction_vector ?? p.direction_vector,
    };
  });
}

/** Snap targets for a port move (all model-space): the equipment's eight
 * bounding-box corners, plus — for a CAD-backed equipment — the CAD mesh's
 * vertices (already reduced to model space by the caller). Feeding both into
 * one list lets the controller reuse the same screen-space nearest search it
 * uses for cell-corner magnetism. */
export function portSnapTargets(
  box: { origin: Vec3; size: Vec3 },
  cadVertices: Vec3[] = [],
): Vec3[] {
  const corners = boxCorners(box);
  return cadVertices.length ? corners.concat(cadVertices) : corners;
}

/** One entry in a contextual type-picker popup (the +Opening / +Equipment
 * button menus). Derived from the engine-advertised type list — never a
 * hardcoded set — with the same "(code|db)" origin tag the old dropdowns used. */
export interface TypePickerItem {
  key: string;
  label: string;
  slug: string;
  origin: string;
}

/** Build picker items from an engine-derived type list (equipment or opening
 * types share the {slug,name,origin} shape). Empty in → empty out. */
export function typePickerItems(
  types: { slug: string; name: string; origin: string }[],
): TypePickerItem[] {
  return types.map((t) => ({
    key: t.slug,
    label: `${t.name} (${t.origin === "code" ? "code" : "db"})`,
    slug: t.slug,
    origin: t.origin,
  }));
}
