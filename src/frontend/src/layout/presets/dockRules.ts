// The docked preset's rules, as data and pure functions: its zones, where each
// panel docks, which tab a zone shows, the grid the zones make around the
// viewport. Kept apart from the preset's components (`./dock`) so `node --test`
// covers them without a canvas.
//
// The document of the docked preset says WHERE a panel is: `zones[z].panels`
// lists the panels docked in `z`, in order, and `active` the tab it shows. A
// panel bound to a visibility flag keeps the flag as its truth for WHETHER it
// shows, as in the classic preset; a panel without one is open while it is in a
// zone's list (`./reducers`). A panel the document does not place docks where
// its `placement.docked` says, so an empty document is the default layout.

import {DOCKED_PRESET_ID, DOCKED_ZONES, CLASSIC_PRESET_ID, CLASSIC_ZONES, type LayoutDoc, type ZoneDef, type ZoneState} from "../layout";
import {placementFor, type PanelId, type PanelPlacement, type RegisteredPanel, type ZoneId} from "../panel";
import {adoptNewPanels, emptyLayoutDoc, openPanel} from "../reducers";

/** The docked preset's zones. The top bar is chrome, not a zone. */
export const DOCKED_ZONE_DEFS: readonly ZoneDef[] = [
  {id: DOCKED_ZONES.left, kind: "dock", label: "Left", edge: "left", limits: {min: 200, max: 560, default: 300}, arrangement: "tabs"},
  {id: DOCKED_ZONES.leftAux, kind: "dock", label: "Left, inner", edge: "left", limits: {min: 220, max: 640, default: 360}, arrangement: "tabs"},
  {id: DOCKED_ZONES.rightAux2, kind: "dock", label: "Right, innermost", edge: "right", limits: {min: 240, max: 560, default: 320}, arrangement: "stack"},
  {id: DOCKED_ZONES.rightAux, kind: "dock", label: "Right, inner", edge: "right", limits: {min: 240, max: 560, default: 320}, arrangement: "stack"},
  {id: DOCKED_ZONES.right, kind: "dock", label: "Right", edge: "right", limits: {min: 260, max: 640, default: 360}, arrangement: "tabs"},
  {id: DOCKED_ZONES.bottom, kind: "dock", label: "Bottom", edge: "bottom", limits: {min: 120, max: 640, default: 280}, arrangement: "tabs"},
  {id: DOCKED_ZONES.float, kind: "float", label: "Windows"},
  {id: DOCKED_ZONES.window, kind: "float", label: "Viewer windows"},
  {id: DOCKED_ZONES.overlay, kind: "overlay", label: "Canvas overlays"},
];

/** The dock zones, outermost left to outermost right, then the bottom. */
export const DOCK_ZONE_DEFS: readonly ZoneDef[] = DOCKED_ZONE_DEFS.filter((z) => z.kind === "dock");

const DOCK_IDS: ReadonlySet<ZoneId> = new Set(DOCK_ZONE_DEFS.map((z) => z.id));

export function isDockZone(zone: ZoneId | undefined): boolean {
  return zone !== undefined && DOCK_IDS.has(zone);
}

/** Width (height for the bottom) of a collapsed zone's strip, in px. */
export const COLLAPSED_STRIP = 32;

/**
 * A panel's placement in the docked preset: its own `docked` (or `default`)
 * entry, else -- for a panel the classic UI stacks in its menu column, such as a
 * plugin's top panel -- the inner right stack, in its classic order.
 */
export function dockedPlacement(panel: Pick<RegisteredPanel, "placement">): PanelPlacement | undefined {
  const own = placementFor(panel, DOCKED_PRESET_ID);
  if (own) return own;
  const classic = panel.placement?.[CLASSIC_PRESET_ID];
  if (classic && classic.zone === CLASSIC_ZONES.menuColumn && !classic.parent) {
    return {zone: DOCKED_ZONES.rightAux, order: classic.order};
  }
  return undefined;
}

/** The zone a panel docks in: where the document has it, else its placement. */
export function dockedZoneOf(doc: LayoutDoc, panel: RegisteredPanel): ZoneId | undefined {
  for (const [zone, state] of Object.entries(doc.zones)) if (state.panels.includes(panel.id)) return zone;
  const pl = dockedPlacement(panel);
  return pl && !pl.parent ? pl.zone : undefined;
}

/**
 * The panels docked in `zone`: the document's order first, then the panels it
 * does not place, by placement order (then registration order).
 */
export function dockZonePanels(doc: LayoutDoc, zone: ZoneId, panels: readonly RegisteredPanel[]): RegisteredPanel[] {
  const listed = doc.zones[zone]?.panels ?? [];
  const byId = new Map(panels.map((p) => [p.id, p]));
  const placed = listed.map((id) => byId.get(id)).filter((p): p is RegisteredPanel => !!p);
  const rest = panels
    .map((p, i) => ({p, i}))
    .filter(({p}) => !listed.includes(p.id) && dockedZoneOf(doc, p) === zone)
    .sort((a, b) => (dockedPlacement(a.p)?.order ?? 0) - (dockedPlacement(b.p)?.order ?? 0) || a.i - b.i)
    .map(({p}) => p);
  return [...placed, ...rest];
}

/** The tab a zone shows: its `active` panel when that is open, else the first open one. */
export function shownTab(state: ZoneState | undefined, open: readonly PanelId[]): PanelId | null {
  if (state?.active && open.includes(state.active)) return state.active;
  return open[0] ?? null;
}

/**
 * Make `id` the tab `zone` shows. A document keeps `active` only for a panel it
 * lists in the zone, so a panel it does not list yet is listed first: `order` is
 * the zone's panels as shown (`dockZonePanels`), which then become the zone's list.
 */
export function activateInZone(doc: LayoutDoc, zone: ZoneId, id: PanelId, order: readonly PanelId[] = []): LayoutDoc {
  const state = doc.zones[zone];
  if (!state || (state.active === id && state.panels.includes(id))) return doc;
  const panels = state.panels.includes(id) ? state.panels : [...new Set([...order, ...state.panels, id])];
  return {...doc, zones: {...doc.zones, [zone]: {...state, panels, active: id}}};
}

/** A zone's track size: nothing when it shows no panel, the strip when collapsed, else its size within its limits. */
export function trackSize(def: ZoneDef, state: ZoneState | undefined, shows: boolean): number {
  if (!shows) return 0;
  if (state?.collapsed) return COLLAPSED_STRIP;
  const lim = def.limits;
  const size = state?.size ?? lim?.default ?? 0;
  return lim ? Math.min(lim.max, Math.max(lim.min, size)) : size;
}

export const VIEWPORT_AREA = "viewport";
export const TOOLBAR_AREA = "toolbar";

/** A zone's grid area name (CSS idents cannot start with a digit, and `-` is fine). */
export const zoneArea = (zone: ZoneId) => `zone-${zone}`;

/**
 * The frame's grid: the top bar across the top, the side docks full height
 * (outer to inner), the viewport in the middle with the bottom dock under it.
 * `tracks` gives each dock zone's size in px (0 hides it).
 */
export function dockGrid(tracks: Readonly<Record<ZoneId, number>>): {
  gridTemplateColumns: string;
  gridTemplateRows: string;
  gridTemplateAreas: string;
} {
  const Z = DOCKED_ZONES;
  const px = (z: ZoneId) => `${tracks[z] ?? 0}px`;
  const cols = [Z.left, Z.leftAux, VIEWPORT_AREA, Z.rightAux2, Z.rightAux, Z.right];
  const row = (mid: string) => cols.map((c) => (c === VIEWPORT_AREA ? mid : zoneArea(c))).join(" ");
  return {
    gridTemplateColumns: `${px(Z.left)} ${px(Z.leftAux)} minmax(0, 1fr) ${px(Z.rightAux2)} ${px(Z.rightAux)} ${px(Z.right)}`,
    gridTemplateRows: `auto minmax(0, 1fr) ${px(Z.bottom)}`,
    gridTemplateAreas: [
      `"${cols.map(() => TOOLBAR_AREA).join(" ")}"`,
      `"${row(VIEWPORT_AREA)}"`,
      `"${row(zoneArea(Z.bottom))}"`,
    ].join(" "),
  };
}

/**
 * The docked document a user starts from. Bound panels are placed by their
 * `placement.docked` and need no entry; a panel without a binding is opened in
 * its dock zone (it has no flag to say so). Every registered panel counts as offered.
 */
export function dockedDefaults({context, panels}: {context: string; panels: RegisteredPanel[]}): LayoutDoc {
  const doc = emptyLayoutDoc(DOCKED_PRESET_ID, DOCKED_ZONE_DEFS, context);
  return adoptDocked(doc, panels);
}

/**
 * Adopt panels registered since the document was saved (`known`): an unbound
 * panel placed in a dock zone is opened there once. Bound panels need nothing:
 * the document places them by default and their flag opens them.
 */
export function adoptDocked(doc: LayoutDoc, panels: readonly RegisteredPanel[]): LayoutDoc {
  const {doc: next, adopted} = adoptNewPanels(doc, panels.map((p) => p.id));
  let out = next;
  for (const id of adopted) {
    const p = panels.find((x) => x.id === id)!;
    const pl = dockedPlacement(p);
    if (p.binding || !pl || pl.parent || !isDockZone(pl.zone)) continue;
    out = openPanel(out, id, {zone: pl.zone, order: (o) => dockedPlacement(panels.find((x) => x.id === o) ?? {})?.order});
    // Opening makes it active; the first panel of a zone stays the shown tab.
    const first = out.zones[pl.zone].panels[0];
    out = activateInZone(out, pl.zone, first);
  }
  return out;
}
