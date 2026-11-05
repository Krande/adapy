// Pure reducers over a layout document (`LayoutDoc`, see `./layout`).
//
// A document records where the panels whose visibility the LAYOUT owns are: the
// panels open in each zone (in order, with the active one), the floating ones
// with their rects, and which panels the user has already been offered. A panel
// bound to an existing visibility flag (`PanelSpec.binding`) keeps that flag as
// its truth, and the document records only WHERE it shows (a window, a group of
// the split grid); these reducers also serve unbound panels (a shell's own),
// which the document opens and closes.
//
// Every function returns a new document and leaves its input untouched.
// Dependency-free, so `node --test` covers them directly.

import type { FloatState, LayoutDoc, ZoneDef, ZoneState } from "./layout";
import type { PanelId, PresetId, ZoneId } from "./panel";
import { DEFAULT_GRID, dockInGrid, gridPanels, normaliseGrid, removeFromGrid, type GridNode, type GridTarget } from "./splitTree";

/** Bumped when the document's shape changes; an older document is migrated or reset. */
export const LAYOUT_DOC_VERSION = 2;

const emptyZone = (): ZoneState => ({ panels: [], active: null });

/** A document with every zone of the preset empty and no panel known. */
export function emptyLayoutDoc(preset: PresetId, zones: readonly ZoneDef[], context = "default"): LayoutDoc {
  const z: Record<ZoneId, ZoneState> = {};
  for (const def of zones) {
    z[def.id] = def.limits ? { ...emptyZone(), size: def.limits.default } : emptyZone();
  }
  return { v: LAYOUT_DOC_VERSION, preset, context, zones: z, grid: DEFAULT_GRID, floats: {}, minimised: [], known: [] };
}

/** Where a panel is open in `doc`: a zone id, `"float"`, `"grid"`, or null when it is closed. */
export function whereOpen(doc: LayoutDoc, id: PanelId): ZoneId | "float" | "grid" | null {
  if (doc.floats[id]) return "float";
  if (isInGrid(doc, id)) return "grid";
  for (const [zone, state] of Object.entries(doc.zones)) if (state.panels.includes(id)) return zone;
  return null;
}

export function isOpenIn(doc: LayoutDoc, id: PanelId): boolean {
  return whereOpen(doc, id) !== null;
}

/** Remove `id` from wherever it is open; the next panel of its zone becomes active. */
export function closePanel(doc: LayoutDoc, id: PanelId): LayoutDoc {
  let changed = false;
  const zones: Record<ZoneId, ZoneState> = {};
  for (const [zone, state] of Object.entries(doc.zones)) {
    const at = state.panels.indexOf(id);
    if (at < 0) {
      zones[zone] = state;
      continue;
    }
    changed = true;
    const panels = state.panels.filter((p) => p !== id);
    const active = state.active === id ? (panels[Math.min(at, panels.length - 1)] ?? null) : state.active;
    zones[zone] = { ...state, panels, active };
  }
  const floats = { ...doc.floats };
  if (floats[id]) {
    delete floats[id];
    changed = true;
  }
  const minimised = doc.minimised.filter((p) => p !== id);
  if (minimised.length !== doc.minimised.length) changed = true;
  const grid = removeFromGrid(gridOf(doc), id);
  if (grid !== gridOf(doc)) changed = true;
  return changed ? { ...doc, zones, grid, floats, minimised } : doc;
}

/** Where to open a panel. */
export interface OpenTarget {
  zone: ZoneId;
  /** Open as a floating window (the zone is the float zone it belongs to). */
  float?: boolean;
  /** First rect when floating and none is remembered. */
  rect?: { x: number; y: number; w: number; h: number };
  /** Sort key within the zone; panels without one go last, in opening order. */
  order?: (id: PanelId) => number | undefined;
}

function topZ(doc: LayoutDoc): number {
  return Object.values(doc.floats).reduce((m, f) => Math.max(m, f.z), 0);
}

/**
 * Open `id` at `target` and make it the active panel there (a float comes to the
 * front). Already open in that place: only activated. Open elsewhere: moved.
 * Every panel in `exclusive` (the rest of its exclusive group) is closed first.
 */
export function openPanel(
  doc: LayoutDoc,
  id: PanelId,
  target: OpenTarget,
  exclusive: readonly PanelId[] = [],
): LayoutDoc {
  let next = doc;
  for (const other of exclusive) if (other !== id) next = closePanel(next, other);
  next = { ...next, minimised: next.minimised.filter((p) => p !== id) };

  if (target.float) {
    const prev = next.floats[id];
    // Docked somewhere: undocked first.
    if (!prev) next = closePanel(next, id);
    const r = prev ?? { x: target.rect?.x ?? 80, y: target.rect?.y ?? 80, w: target.rect?.w ?? 480, h: target.rect?.h ?? 360 };
    const float: FloatState = { x: r.x, y: r.y, w: r.w, h: r.h, z: topZ(next) + 1 };
    return { ...next, floats: { ...next.floats, [id]: float } };
  }

  const where = whereOpen(next, id);
  if (where !== null && where !== target.zone) next = closePanel(next, id);
  const zone = next.zones[target.zone] ?? emptyZone();
  if (zone.panels.includes(id)) {
    return { ...next, zones: { ...next.zones, [target.zone]: { ...zone, active: id } } };
  }
  const panels = [...zone.panels, id];
  if (target.order) {
    const key = (p: PanelId) => target.order!(p) ?? Number.POSITIVE_INFINITY;
    // Stable: equal keys keep their opening order.
    panels.sort((a, b) => key(a) - key(b));
  }
  return { ...next, zones: { ...next.zones, [target.zone]: { ...zone, panels, active: id } } };
}

/** Move an open (or closed) panel into `zone` at `index` (clamped), making it active there. */
export function movePanel(doc: LayoutDoc, id: PanelId, zone: ZoneId, index: number): LayoutDoc {
  const next = closePanel(doc, id);
  const state = next.zones[zone] ?? emptyZone();
  const panels = [...state.panels];
  panels.splice(Math.max(0, Math.min(index, panels.length)), 0, id);
  return { ...next, zones: { ...next.zones, [zone]: { ...state, panels, active: id } } };
}

/** A zone's size, clamped to its limits when the preset gives some. */
export function setZoneSize(doc: LayoutDoc, zone: ZoneId, size: number, def?: ZoneDef): LayoutDoc {
  const state = doc.zones[zone];
  if (!state) return doc;
  const lim = def?.limits;
  const s = lim ? Math.min(lim.max, Math.max(lim.min, size)) : size;
  return { ...doc, zones: { ...doc.zones, [zone]: { ...state, size: s } } };
}

export function setZoneCollapsed(doc: LayoutDoc, zone: ZoneId, collapsed: boolean): LayoutDoc {
  const state = doc.zones[zone];
  if (!state) return doc;
  return { ...doc, zones: { ...doc.zones, [zone]: { ...state, collapsed } } };
}

/** Bring a float to the front. */
export function raiseFloat(doc: LayoutDoc, id: PanelId): LayoutDoc {
  const f = doc.floats[id];
  if (!f) return doc;
  const z = topZ(doc);
  if (f.z === z) return doc;
  return { ...doc, floats: { ...doc.floats, [id]: { ...f, z: z + 1 } } };
}

// ---------------------------------------------------------------------------
// Undocking. For a panel bound to its own visibility flag, `floats` and
// `minimised` record WHERE it shows, not WHETHER: the flag still opens and closes
// it, and a floated panel keeps its window across a close.
// ---------------------------------------------------------------------------

/** Float `id` at `rect` (or where it last floated), on top, not minimised, out of the grid. */
export function floatPanel(doc: LayoutDoc, id: PanelId, rect: { x: number; y: number; w: number; h: number }): LayoutDoc {
  const prev = doc.floats[id];
  const r = prev ?? rect;
  return {
    ...withoutGrid(doc, id),
    floats: { ...doc.floats, [id]: { x: r.x, y: r.y, w: r.w, h: r.h, z: topZ(doc) + 1 } },
    minimised: doc.minimised.filter((p) => p !== id),
  };
}

/** Float `id` with its title bar where `rect` puts it, keeping a remembered size. */
export function floatPanelAt(doc: LayoutDoc, id: PanelId, rect: { x: number; y: number; w: number; h: number }): LayoutDoc {
  const next = floatPanel(doc, id, rect);
  const f = next.floats[id];
  return f.x === rect.x && f.y === rect.y ? next : { ...next, floats: { ...next.floats, [id]: { ...f, x: rect.x, y: rect.y } } };
}

/** A floated panel's new rect. */
export function setFloatRect(doc: LayoutDoc, id: PanelId, rect: { x: number; y: number; w: number; h: number }): LayoutDoc {
  const f = doc.floats[id];
  if (!f || (f.x === rect.x && f.y === rect.y && f.w === rect.w && f.h === rect.h)) return doc;
  return { ...doc, floats: { ...doc.floats, [id]: { ...f, ...rect } } };
}

/** Back to its place in the layout: no window, not minimised, not in the grid. */
export function dockPanel(doc: LayoutDoc, id: PanelId): LayoutDoc {
  const next = withoutGrid(doc, id);
  if (!next.floats[id] && !next.minimised.includes(id)) return next;
  const floats = { ...next.floats };
  delete floats[id];
  return { ...next, floats, minimised: next.minimised.filter((p) => p !== id) };
}

// ---------------------------------------------------------------------------
// The split grid (`./splitTree`). A panel is in at most one place: its own
// place in the preset, a window (`floats`), or a group of the grid.
// ---------------------------------------------------------------------------

/** The document's grid; the viewport alone when it has none. */
export const gridOf = (doc: LayoutDoc): GridNode => doc.grid ?? DEFAULT_GRID;

/** Is `id` in a group of the grid. */
export function isInGrid(doc: LayoutDoc, id: PanelId): boolean {
  return gridPanels(gridOf(doc)).includes(id);
}

function withoutGrid(doc: LayoutDoc, id: PanelId): LayoutDoc {
  const grid = gridOf(doc);
  const next = removeFromGrid(grid, id);
  return next === grid ? doc : { ...doc, grid: next };
}

/** Dock `id` in the grid at `target`, out of its window and the tray. A drop that moves nothing is no change. */
export function dockInGridDoc(doc: LayoutDoc, id: PanelId, target: GridTarget): LayoutDoc {
  const grid = dockInGrid(gridOf(doc), id, target);
  if (grid === gridOf(doc)) return doc;
  const floats = { ...doc.floats };
  delete floats[id];
  return { ...doc, grid, floats, minimised: doc.minimised.filter((p) => p !== id) };
}

/** Apply a change to the grid. */
export function updateGrid(doc: LayoutDoc, fn: (grid: GridNode) => GridNode): LayoutDoc {
  const grid = gridOf(doc);
  const next = fn(grid);
  return next === grid ? doc : { ...doc, grid: next };
}

export function minimisePanel(doc: LayoutDoc, id: PanelId): LayoutDoc {
  if (doc.minimised.includes(id)) return doc;
  return { ...doc, minimised: [...doc.minimised, id] };
}

/** Out of the tray; a float comes to the front. */
export function restorePanel(doc: LayoutDoc, id: PanelId): LayoutDoc {
  if (!doc.minimised.includes(id)) return doc;
  const next = { ...doc, minimised: doc.minimised.filter((p) => p !== id) };
  return raiseFloat(next, id);
}

/**
 * Panels registered since the document was saved. Each is recorded as known
 * exactly once, so a layout can offer it (open it in its default place, show it
 * in a menu) the first time and then leave the user's choice alone.
 */
export function adoptNewPanels(doc: LayoutDoc, registered: readonly PanelId[]): { doc: LayoutDoc; adopted: PanelId[] } {
  const known = new Set(doc.known);
  const adopted = registered.filter((id) => !known.has(id));
  if (adopted.length === 0) return { doc, adopted };
  return { doc: { ...doc, known: [...doc.known, ...adopted] }, adopted };
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/**
 * A stored document made safe to use with `preset`: a missing, foreign or
 * older-version document becomes the empty one; zones the preset no longer has
 * are dropped and new ones added; malformed entries are dropped, and a grid
 * that is not a valid one is the viewport alone (`normaliseGrid`). Panel ids are
 * kept even when nothing has registered them (yet): a plugin may register later,
 * and a host renders an unknown id as absent rather than deleting it.
 */
export function migrateLayoutDoc(raw: unknown, preset: PresetId, zones: readonly ZoneDef[], context = "default"): LayoutDoc {
  const fresh = emptyLayoutDoc(preset, zones, context);
  if (!isObj(raw) || raw.v !== LAYOUT_DOC_VERSION || raw.preset !== preset) return fresh;
  const rawZones = isObj(raw.zones) ? raw.zones : {};
  const out: Record<ZoneId, ZoneState> = {};
  for (const def of zones) {
    const z = rawZones[def.id];
    if (!isObj(z)) {
      out[def.id] = fresh.zones[def.id];
      continue;
    }
    const panels = [...new Set(strings(z.panels))];
    const active = typeof z.active === "string" && panels.includes(z.active) ? z.active : (panels[0] ?? null);
    const state: ZoneState = { panels, active };
    if (num(z.size)) state.size = z.size;
    else if (fresh.zones[def.id].size !== undefined) state.size = fresh.zones[def.id].size;
    if (typeof z.collapsed === "boolean") state.collapsed = z.collapsed;
    out[def.id] = state;
  }
  const grid = normaliseGrid(raw.grid);
  const docked = new Set(gridPanels(grid));
  const floats: Record<PanelId, FloatState> = {};
  if (isObj(raw.floats)) {
    for (const [id, f] of Object.entries(raw.floats)) {
      // A panel is in one place: the grid's, when both list it.
      if (docked.has(id)) continue;
      if (isObj(f) && num(f.x) && num(f.y) && num(f.w) && num(f.h) && f.w > 0 && f.h > 0) {
        floats[id] = { x: f.x, y: f.y, w: f.w, h: f.h, z: num(f.z) ? f.z : 0, ...(f.minimised === true ? { minimised: true } : {}) };
      }
    }
  }
  return {
    v: LAYOUT_DOC_VERSION,
    preset,
    context: typeof raw.context === "string" ? raw.context : context,
    zones: out,
    grid,
    floats,
    minimised: [...new Set(strings(raw.minimised))],
    known: [...new Set(strings(raw.known))],
    ...(num(raw.shellVersion) ? { shellVersion: raw.shellVersion } : {}),
  };
}
