// Where a panel shows in the classic preset, and the moves between places.
//
// Every core panel has a HOME, the place the classic UI has always given it: the
// panel column, the left tree track, or a window of its own (the attribute search,
// the admin / convert window). With "Customisable panels"
// on, a panel with frameless content may also show in a window of the layout
// (`floats`; for a panel at home in a window, its own window is that) or in a
// group of the split grid around the viewport (`grid`, `../splitTree`). The
// classic document records where; the panel's flag still says whether.
//
// The moves are the document's: floating, docking at an edge of the view or of
// a group, stacking with a group, going home. A header's menu and a drop both
// end here.

import {defaultRect} from "@/assets/floatingWindow";
import {ExternalGlyph, MoveGlyph} from "@/components/ui/PanelFrame";

import type {DropTarget} from "../dockDrag";
import {CORE_PANEL_IDS, CORE_PANEL_OWNER} from "../corePanelIds";
import {CLASSIC_PRESET_ID, CLASSIC_ZONES, DOCKED_PRESET_ID, type LayoutDoc} from "../layout";
import {useCustomisablePanels as useServedCustomisablePanels} from "../customisable";
import type {PanelAction, PanelId, PanelMenuItem, RegisteredPanel} from "../panel";
import {adoptNewPanels, dockInGridDoc, dockPanel, floatPanel, floatPanelAt, gridOf} from "../reducers";
import {canPopOut} from "../popout";
import {getPanel, isPanelAvailable, listPanels} from "../registry";
import {hide, reveal} from "../reveal";
import {moveToWindowItem, openNewWindow, windowBus} from "../windows";
import {VIEWPORT_NODE, findNode, gridGroups, groupOf, type DockEdge, type GridTarget} from "../splitTree";
import {offeredInMode} from "../shellModes";
import {requestFloatFocus, restoreFocus} from "../zoneFocus";
import {PANEL_ACTIONS_ATTR} from "../zoneKeys";
import {arrangementOf, placeOf, servedClassicShell, updateClassic, type Place} from "./classicShell";

// The served shell and its documents (`./classicShell`) and the memory of open
// panels (`./classicMemory`) are re-exported, so the classic frame and plugins
// keep importing the classic preset from this one module.
export * from "./classicShell";
export * from "./classicMemory";

/** On a group of the grid: its id. */
export const GRID_GROUP_ATTR = "data-grid-group";
/** On a group's tab: the panel it shows. */
export const GRID_TAB_ATTR = "data-grid-tab";
/** On the grid's container. */
export const GRID_ROOT_ATTR = "data-grid-root";

/** A grid node as a drop zone, and back. */
export const gridZone = (node: string) => `grid:${node}`;
export const gridNodeOfZone = (zone: string): string | null => (zone.startsWith("grid:") ? zone.slice(5) : null);

/** Are customisable panels on in the served UI (`../customisable`: always in a composed shell, else the user's setting for it). */
export const useCustomisablePanels = useServedCustomisablePanels;

/** A plugin panel the served shell shows over the view (it names the `overlay` dock). */
export function isOverlayPanel(p: RegisteredPanel): boolean {
  return !!servedClassicShell()?.overlays?.pluginPanels && p.owner !== CORE_PANEL_OWNER && dockOf(p) === "overlay";
}

/** Where a dock id goes in the grid when the shell maps it to no group that exists: that edge of the view. */
const DOCK_EDGE: Readonly<Record<string, DockEdge>> = {left: "left", bottom: "bottom"};

/**
 * In a composed shell, place each plugin panel that names a dock, once (the
 * document's `known`): into the group the shell maps that dock to, else at that
 * edge of the view. After that it is the user's to move. A floating dock is
 * given a window of the layout, once, in core's own UI as well; an overlay
 * dock is left to its region. A panel that asks to start in a window
 * (`PanelPlacement.startFloated`), core's own included, is given one the same way.
 */
export function adoptDockedPanels(): void {
  // A dock in the grid is a composed shell's to map; a window is any layout's.
  const docks = servedClassicShell() ? (arrangementOf().docks ?? {}) : null;
  // Per mode: a panel offered only in other modes is adopted when one of those is entered.
  // An unavailable one (a dormant or disabled plugin's) is placed when it becomes available.
  const candidates = listPanels().filter((p) => {
    if (!offeredInMode(p)) return false;
    // Placed whether or not it is offered right now: its window is where it shows once it is.
    if (startsFloated(p)) return canFloat(p);
    if (p.owner === CORE_PANEL_OWNER || !isPanelAvailable(p, {})) return false;
    const dock = dockOf(p);
    return dock === "float" ? canFloat(p) : docks !== null && dock !== null && canDock(p);
  });
  if (!candidates.length) return;
  // A set, not reset per call: the store may run the update more than once.
  const floated = new Set<PanelId>();
  updateClassic((d) => {
    const {doc, adopted} = adoptNewPanels(d, candidates.map((p) => p.id));
    let out = doc;
    for (const id of adopted) {
      const p = getPanel(id)!;
      if (startsFloated(p)) {
        floated.add(id);
        continue;
      }
      const dock = dockOf(p)!;
      // A floating dock starts in a window of the layout, below; an overlay stays with its region.
      if (dock === "float") floated.add(id);
      if (dock === "float" || dock === "overlay" || !docks) continue;
      const group = docks[dock];
      const target: GridTarget =
        group && findNode(gridOf(out), group) ? {node: group, edge: "center"} : {node: VIEWPORT_NODE, edge: DOCK_EDGE[dock] ?? "right"};
      out = dockInGridDoc(out, id, target);
    }
    return out;
  });
  // Where it floats the first time; it shows there once it is opened.
  for (const id of floated) floatOut(id);
}

/** Plugin panels shown in a place of the layout's own (a group, a window) rather than their region. */
export function placedElsewhere(doc: LayoutDoc, customisable: boolean, narrow: boolean): PanelId[] {
  return listPanels()
    .filter(
      (p) =>
        (p.owner !== CORE_PANEL_OWNER || isHostedSection(p)) && (shownPlace(doc, p.id, customisable, narrow) !== "home" || isOverlayPanel(p)),
    )
    .map((p) => p.id);
}

// ---------------------------------------------------------------------------
// Places.
// ---------------------------------------------------------------------------

export type Home = "column" | "tree" | "window";

/** Where the classic UI puts a panel by default; null for one hosted inside another, or placed nowhere. */
export function homeOf(panel: Pick<RegisteredPanel, "placement">): Home | null {
  const pl = panel.placement?.[CLASSIC_PRESET_ID];
  if (!pl || pl.parent) return null;
  if (pl.zone === CLASSIC_ZONES.menuColumn) return "column";
  if (pl.zone === CLASSIC_ZONES.tree) return "tree";
  if (pl.zone === CLASSIC_ZONES.float || pl.zone === CLASSIC_ZONES.window) return "window";
  return null;
}

/** Does the panel ask to start in a window of the layout (`PanelPlacement.startFloated`). */
export function startsFloated(panel: Pick<RegisteredPanel, "placement"> | undefined): boolean {
  return !!panel?.placement?.[CLASSIC_PRESET_ID]?.startFloated;
}

/** The dock a plugin panel asks for (`PanelSlot.dock`), if any. */
export function dockOf(panel: Pick<RegisteredPanel, "placement">): string | null {
  return panel.placement?.[DOCKED_PRESET_ID]?.zone ?? null;
}

/**
 * May leave its home for the grid: it allows docking, has frameless content, and
 * has a home of its own or names a dock (a plugin panel otherwise shown inside a
 * core panel).
 */
export function canDock(panel: RegisteredPanel): boolean {
  return panel.capabilities.dockable && !!panel.content && (homeOf(panel) !== null || dockOf(panel) !== null || isHostedSection(panel));
}

/**
 * A panel the classic UI draws inside another (`placement.parent`: the result
 * data table in the Simulation panel). Core's own UI keeps it there; a composed
 * shell may dock it, and where its grid places it, it is shown (and it leaves its host).
 */
export function isHostedSection(panel: Pick<RegisteredPanel, "placement">): boolean {
  return !!servedClassicShell() && !!panel.placement?.[CLASSIC_PRESET_ID]?.parent;
}

/**
 * May float in a window of the layout: it allows floating, has frameless content
 * and is at home in the column or the tree track, or names a dock (a panel at
 * home in a window floats in that one).
 */
export function canFloat(panel: RegisteredPanel): boolean {
  const home = homeOf(panel);
  return panel.capabilities.floatable && !!panel.content && (home === "column" || home === "tree" || (home === null && dockOf(panel) !== null));
}

/**
 * Where a panel shows right now: with the setting off, at home; below `wide`,
 * where there is no room for the grid, a panel of the grid at home too. So is a
 * panel that starts in a window (`startsFloated`): a window takes the whole
 * screen there, which is the user's to ask for of a panel they floated, not what
 * a panel that opens beside the view is to do by itself.
 */
export function shownPlace(doc: LayoutDoc, id: PanelId, customisable: boolean, narrow: boolean): Place {
  if (!customisable) return "home";
  const p = placeOf(doc, id);
  if (!narrow) return p;
  return p === "grid" || (p === "float" && startsFloated(getPanel(id))) ? "home" : p;
}

/** The label of the move back home. */
export const HOME_LABEL: Readonly<Record<Home, string>> = {
  column: "Back to the panel column",
  tree: "Back to the left track",
  window: "Float in its own window",
};

/** The label of a panel's move back home: the mode helper's home is a card at the top of the view, not the column. */
export function homeLabel(panel: Pick<RegisteredPanel, "id">, home: Home): string {
  return panel.id === CORE_PANEL_IDS.modeHelper ? "Back to the top" : HOME_LABEL[home];
}

// ---------------------------------------------------------------------------
// Moves.
// ---------------------------------------------------------------------------

const FIRST_SIZE = {w: 420, h: 560};
export const FLOAT_MIN_SIZE = {width: 280, height: 160};
const CASCADE = 32;
/** Where a window floated at the pointer is held: this far into its title bar. */
const GRAB = {x: 60, y: 16};

/**
 * Float a panel: where it last floated, else at its first-open size near the top
 * right, stepped down and left past the windows already open; or, given `at`,
 * with its title bar under that pointer. A panel that says where its first
 * window goes (`PanelPlacement.floatAt`) floats there instead of near the top
 * right. A panel at home in a window goes back to that window.
 */
export function floatOut(id: PanelId, at?: {x: number; y: number}): void {
  const p = getPanel(id);
  if (!p) return;
  if (homeOf(p) === "window") {
    updateClassic((d) => dockPanel(d, id));
    return;
  }
  const placement = p.placement?.[CLASSIC_PRESET_ID];
  const size = placement?.float ?? FIRST_SIZE;
  const own = placement?.floatAt;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const height = own?.maxHeight ? Math.min(size.h, Math.round(vh * own.maxHeight)) : size.h;
  const r = defaultRect({width: vw, height: vh}, {width: size.w, height}, FLOAT_MIN_SIZE);
  updateClassic((d) => {
    if (at) return floatPanelAt(d, id, {x: Math.max(0, at.x - GRAB.x), y: Math.max(0, at.y - GRAB.y), w: r.width, h: r.height});
    if (own) return floatPanel(d, id, {x: Math.max(0, vw - r.width - own.right), y: own.top, w: r.width, h: r.height});
    const step = Object.keys(d.floats).length * CASCADE;
    return floatPanel(d, id, {x: Math.max(0, vw - r.width - 80 - step), y: r.y + step, w: r.width, h: r.height});
  });
}

/** Back home: out of its window and the grid. */
export function backHome(id: PanelId): void {
  updateClassic((d) => dockPanel(d, id));
}

/** Dock in the grid at `target`. */
export function dockAt(id: PanelId, target: GridTarget): void {
  updateClassic((d) => dockInGridDoc(d, id, target));
}

/**
 * A panel dragged by its title bar, tab or header, dropped at `target`: into
 * the grid there, or home onto the panel column. False when the target does not
 * take it (or there is none), so the caller floats it or leaves it.
 */
export function dropPanel(id: PanelId, target: DropTarget | null): boolean {
  const p = getPanel(id);
  if (!target || !p) return false;
  if (target.zone === CLASSIC_ZONES.menuColumn) {
    if (homeOf(p) !== "column" || p.id === CORE_PANEL_IDS.modeHelper) return false;
    backHome(id);
    return true;
  }
  const node = gridNodeOfZone(target.zone);
  if (!node || !canDock(p)) return false;
  if (target.index !== null) dockAt(id, {node, edge: "center", index: target.index});
  else if (target.edge) dockAt(id, {node, edge: target.edge});
  else return false;
  return true;
}

const esc = (s: string) => (typeof CSS !== "undefined" && CSS.escape ? CSS.escape(s) : s.replace(/"/g, '\\"'));

/** Focus the panel where it now is: its tab in the grid, else its header's actions, else its window. */
export function focusPanel(id: PanelId): void {
  requestAnimationFrame(() => {
    const tab = document.querySelector<HTMLElement>(`[${GRID_TAB_ATTR}="${esc(id)}"]`);
    if (tab) {
      tab.focus();
      return;
    }
    if (document.querySelector(`[${PANEL_ACTIONS_ATTR}="${esc(id)}"]`)) restoreFocus(id, null);
  });
}

const EDGE_LABEL: readonly [DockEdge, string][] = [
  ["left", "Dock left of the view"],
  ["right", "Dock right of the view"],
  ["top", "Dock top of the view"],
  ["bottom", "Dock bottom of the view"],
];

/** "Open in a new window", for a panel that may pop out (`../popout`); none otherwise. */
export function popoutActions(panel: RegisteredPanel): PanelAction[] {
  if (!canPopOut(panel)) return [];
  return [
    {
      id: "popout",
      label: "Open in a new window",
      icon: ExternalGlyph,
      run: () => {
        if (openNewWindow(windowBus(), panel.id)) hide(panel.id);
        else console.warn(`[layout] the browser blocked the window for "${panel.id}"`);
      },
    },
  ];
}

/** "Move to window" for a panel of the main window: out of here (its flag closed), back in on a refusal. */
export function mainWindowMoveItem(panel: RegisteredPanel): PanelMenuItem | null {
  return moveToWindowItem(panel, {remove: () => void hide(panel.id), restore: () => void reveal(panel.id)});
}

/** A group's name in a menu: its panels' titles. */
export function groupLabel(panels: readonly PanelId[]): string {
  return panels.map((p) => getPanel(p)?.title ?? p).join(", ");
}

/**
 * "Move panel": float, dock at an edge of the view, stack with a group (named by
 * its panels), or go back home -- each where it applies, the current place marked.
 */
export function moveAction(panel: RegisteredPanel, doc: LayoutDoc): PanelAction {
  const place = placeOf(doc, panel.id);
  const home = homeOf(panel);
  const grid = gridOf(doc);
  const own = groupOf(grid, panel.id);
  const items: PanelMenuItem[] = [];
  const floating = home === "window" ? place === "home" : place === "float";
  if (home === "window" || canFloat(panel)) {
    items.push({
      id: "float",
      label: "Float in a window",
      current: floating,
      disabled: floating,
      run: () => {
        requestFloatFocus(panel.id);
        floatOut(panel.id);
      },
    });
  }
  for (const [edge, label] of EDGE_LABEL) {
    items.push({
      id: `dock-${edge}`,
      label,
      run: () => {
        dockAt(panel.id, {node: VIEWPORT_NODE, edge});
        focusPanel(panel.id);
      },
    });
  }
  for (const g of gridGroups(grid)) {
    if (g.id === own?.id) continue;
    items.push({
      id: `stack-${g.id}`,
      label: `Stack with ${groupLabel(g.panels)}`,
      run: () => {
        dockAt(panel.id, {node: g.id, edge: "center"});
        focusPanel(panel.id);
      },
    });
  }
  if (home === "column" || home === "tree") {
    items.push({
      id: "home",
      label: homeLabel(panel, home),
      current: place === "home",
      disabled: place === "home",
      run: () => {
        backHome(panel.id);
        focusPanel(panel.id);
      },
    });
  }
  const toWindow = mainWindowMoveItem(panel);
  if (toWindow) items.push(toWindow);
  return {id: "move", label: "Move panel", icon: MoveGlyph, run: () => {}, menu: items};
}
