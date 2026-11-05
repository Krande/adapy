// Where a panel shows in the classic preset, and the moves between places.
//
// Every core panel has a HOME, the place the classic UI has always given it: the
// panel column, the left tree track, or a window of its own (the node editor,
// the attribute search, the admin / convert window). With "Customisable panels"
// on, a panel with frameless content may also show in a window of the layout
// (`floats`; for a panel at home in a window, its own window is that) or in a
// group of the split grid around the viewport (`grid`, `../splitTree`). The
// classic document records where; the panel's flag still says whether.
//
// The moves are the document's: floating, docking at an edge of the view or of
// a group, stacking with a group, going home. A header's menu, a drop and
// Ctrl+Alt+arrow all end here.

import {useMemo} from "react";
import {useStore} from "zustand";

import {defaultRect} from "@/assets/floatingWindow";
import {MoveGlyph} from "@/components/ui/PanelFrame";

import type {DropTarget} from "../dockDrag";
import {CLASSIC_PRESET_ID, CLASSIC_ZONES, customisablePanelsOn, layoutDocKey, type LayoutDoc} from "../layout";
import {layoutStore} from "../layoutStore";
import type {PanelAction, PanelId, PanelMenuItem, RegisteredPanel} from "../panel";
import {dockInGridDoc, dockPanel, floatPanel, floatPanelAt, gridOf, isInGrid, migrateLayoutDoc} from "../reducers";
import {getPanel} from "../registry";
import {
  ROOT_NODE,
  VIEWPORT_NODE,
  gridGroups,
  groupOf,
  neighbourRect,
  type DockEdge,
  type Direction,
  type GridTarget,
  type Rect,
} from "../splitTree";
import {requestFloatFocus, restoreFocus} from "../zoneFocus";
import {PANEL_ACTIONS_ATTR, ZONE_ATTR} from "../zoneKeys";
import {CLASSIC_ZONE_DEFS} from "./classicRules";

/** On a group of the grid: its id. */
export const GRID_GROUP_ATTR = "data-grid-group";
/** On a group's tab: the panel it shows. */
export const GRID_TAB_ATTR = "data-grid-tab";
/** On the grid's container. */
export const GRID_ROOT_ATTR = "data-grid-root";

/** A grid node as a drop zone, and back. */
export const gridZone = (node: string) => `grid:${node}`;
export const gridNodeOfZone = (zone: string): string | null => (zone.startsWith("grid:") ? zone.slice(5) : null);

/** Is the "Customisable panels" setting on. */
export function useCustomisablePanels(): boolean {
  return useStore(layoutStore, (s) => customisablePanelsOn(s.prefs));
}

/** The user's classic document, re-rendering when it changes. */
export function useClassicDoc(): LayoutDoc {
  const raw = useStore(layoutStore, (s) => s.prefs.docs[layoutDocKey(CLASSIC_PRESET_ID)]);
  return useMemo(() => migrateLayoutDoc(raw, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS), [raw]);
}

export function classicDoc(): LayoutDoc {
  return layoutStore.getState().doc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS);
}

export function updateClassic(fn: (doc: LayoutDoc) => LayoutDoc): void {
  layoutStore.getState().update(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, fn);
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

/** May leave its home for the grid: it allows docking and has frameless content. */
export function canDock(panel: RegisteredPanel): boolean {
  return panel.capabilities.dockable && !!panel.content && homeOf(panel) !== null;
}

/**
 * May float in a window of the layout: it allows floating, has frameless content
 * and is at home in the column or the tree track (a panel at home in a window
 * floats in that one).
 */
export function canFloat(panel: RegisteredPanel): boolean {
  const home = homeOf(panel);
  return panel.capabilities.floatable && !!panel.content && (home === "column" || home === "tree");
}

export type Place = "home" | "float" | "grid";

export function placeOf(doc: LayoutDoc, id: PanelId): Place {
  if (isInGrid(doc, id)) return "grid";
  if (doc.floats[id]) return "float";
  return "home";
}

/**
 * Where a panel shows right now: with the setting off, at home; below `wide`,
 * where there is no room for the grid, a panel of the grid at home too.
 */
export function shownPlace(doc: LayoutDoc, id: PanelId, customisable: boolean, narrow: boolean): Place {
  if (!customisable) return "home";
  const p = placeOf(doc, id);
  return p === "grid" && narrow ? "home" : p;
}

/** The label of the move back home. */
export const HOME_LABEL: Readonly<Record<Home, string>> = {
  column: "Back to the panel column",
  tree: "Back to the left track",
  window: "Float in its own window",
};

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
 * with its title bar under that pointer. A panel at home in a window goes back
 * to that window.
 */
export function floatOut(id: PanelId, at?: {x: number; y: number}): void {
  const p = getPanel(id);
  if (!p) return;
  if (homeOf(p) === "window") {
    updateClassic((d) => dockPanel(d, id));
    return;
  }
  const size = p.placement?.[CLASSIC_PRESET_ID]?.float ?? FIRST_SIZE;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const r = defaultRect({width: vw, height: vh}, {width: size.w, height: size.h}, FLOAT_MIN_SIZE);
  updateClassic((d) => {
    if (at) return floatPanelAt(d, id, {x: Math.max(0, at.x - GRAB.x), y: Math.max(0, at.y - GRAB.y), w: r.width, h: r.height});
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
    if (homeOf(p) !== "column") return false;
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

const DIR_EDGE: Readonly<Record<Direction, DockEdge>> = {left: "left", right: "right", up: "top", down: "bottom"};

function rectOf(el: Element | null): Rect | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 ? {left: r.left, top: r.top, width: r.width, height: r.height} : null;
}

/**
 * Ctrl+Alt+arrow: to the group that way on screen (stacked in it); with none,
 * a panel in the grid goes to the grid's edge that way, and any other to the
 * view's. A panel alone at the grid's edge that way stays.
 */
export function moveByKey(id: PanelId, dir: Direction): void {
  const panel = getPanel(id);
  if (!panel || !canDock(panel)) return;
  const grid = gridOf(classicDoc());
  const own = groupOf(grid, id);
  const groups = Array.from(document.querySelectorAll(`[${GRID_GROUP_ATTR}]`))
    .map((el) => ({id: el.getAttribute(GRID_GROUP_ATTR)!, rect: rectOf(el)}))
    .filter((g): g is {id: string; rect: Rect} => !!g.rect);
  const from = (own && groups.find((g) => g.id === own.id)?.rect) || rectOf(document.querySelector(`[${ZONE_ATTR}="viewport"]`));
  if (!from) return;
  const next = neighbourRect(from, dir, groups.filter((g) => g.id !== own?.id));
  const edge = DIR_EDGE[dir];
  let target: GridTarget;
  if (next) target = {node: next.id, edge: "center"};
  else if (own) {
    const root = rectOf(document.querySelector(`[${GRID_ROOT_ATTR}]`));
    const atEdge =
      !!root &&
      own.panels.length === 1 &&
      (dir === "left"
        ? from.left <= root.left + 1
        : dir === "right"
          ? from.left + from.width >= root.left + root.width - 1
          : dir === "up"
            ? from.top <= root.top + 1
            : from.top + from.height >= root.top + root.height - 1);
    if (atEdge) return;
    target = {node: ROOT_NODE, edge};
  } else target = {node: VIEWPORT_NODE, edge};
  dockAt(id, target);
  focusPanel(id);
}

const EDGE_LABEL: readonly [DockEdge, string][] = [
  ["left", "Dock left of the view"],
  ["right", "Dock right of the view"],
  ["top", "Dock top of the view"],
  ["bottom", "Dock bottom of the view"],
];

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
      label: HOME_LABEL[home],
      current: place === "home",
      disabled: place === "home",
      run: () => {
        backHome(panel.id);
        focusPanel(panel.id);
      },
    });
  }
  return {id: "move", label: "Move panel", icon: MoveGlyph, run: () => {}, menu: items};
}
