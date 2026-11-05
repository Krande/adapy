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
// a group, stacking with a group, going home. A header's menu and a drop both
// end here.

import {useMemo, useSyncExternalStore} from "react";
import {useStore} from "zustand";

import {defaultRect} from "@/assets/floatingWindow";
import {ExternalGlyph, MoveGlyph} from "@/components/ui/PanelFrame";

import type {DropTarget} from "../dockDrag";
import {CORE_PANEL_OWNER} from "../corePanelIds";
import {CLASSIC_PRESET_ID, CLASSIC_ZONES, DOCKED_PRESET_ID, customisablePanelsOn, layoutDocKey, type LayoutDoc} from "../layout";
import {layoutStore} from "../layoutStore";
import type {PanelAction, PanelId, PanelMenuItem, RegisteredPanel} from "../panel";
import {
  adoptNewPanels,
  dockInGridDoc,
  dockPanel,
  emptyLayoutDoc,
  floatPanel,
  floatPanelAt,
  gridOf,
  isInGrid,
  migrateLayoutDoc,
} from "../reducers";
import {canPopOut, openPopout} from "../popout";
import {getPanel, isPanelAvailable, listPanels} from "../registry";
import {hide} from "../reveal";
import {
  VIEWPORT_NODE,
  findNode,
  gridGroups,
  gridPanels,
  groupOf,
  type DockEdge,
  type GridNode,
  type GridTarget,
} from "../splitTree";
import {activeShellMode, offeredInMode, shellModesVersion, subscribeShellModes} from "../shellModes";
import type {ViewportAnchor} from "../viewportOverlays";
import {requestFloatFocus, restoreFocus} from "../zoneFocus";
import {PANEL_ACTIONS_ATTR} from "../zoneKeys";
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

/** One arrangement of a composed shell: its starting grid, panels to open, dock groups. */
export interface ClassicArrangement {
  grid?: GridNode;
  open?: readonly PanelId[];
  docks?: Readonly<Record<string, string>>;
  /** A panel drawn over the 3D view (the canvas stays mounted under it). */
  cover?: PanelId;
}

/** Extra actions a composed shell gives each group of the grid. */
export type GroupActionId = "collapse" | "pin" | "add";

/** A composed shell as the classic frame serves it: its id, its arrangement, and per-mode overrides. */
export interface ClassicShell extends ClassicArrangement {
  id: string;
  /** By mode id. A mode missing here (a plugin's) uses the shell's arrangement. */
  modes?: Readonly<Record<string, ClassicArrangement>>;
  /** Group chrome: extra header actions, and panel icons in the tabs. */
  groups?: {actions?: readonly GroupActionId[]; tabIcons?: boolean};
  /** HUDs over the view: core's legend and the `overlay`-dock plugin panels, by anchor. */
  overlays?: {legend?: ViewportAnchor; pluginPanels?: ViewportAnchor};
  /** The arrangements' version, and how a saved one of another version is brought up to date. */
  version?: number;
  migrate?: (doc: LayoutDoc, from: number | undefined) => LayoutDoc | null;
}

/** The group chrome the served shell asks for; none for core's own UI. */
export function classicGroupChrome(): {actions: readonly GroupActionId[]; tabIcons: boolean} {
  return {actions: shell?.groups?.actions ?? [], tabIcons: !!shell?.groups?.tabIcons};
}

/** Where the served shell puts HUDs over the view; none for core's own UI. */
export function classicOverlays(): {legend?: ViewportAnchor; pluginPanels?: ViewportAnchor} | null {
  return shell?.overlays ?? null;
}

/** A plugin panel the served shell shows over the view (it names the `overlay` dock). */
export function isOverlayPanel(p: RegisteredPanel): boolean {
  return !!shell?.overlays?.pluginPanels && p.owner !== CORE_PANEL_OWNER && dockOf(p) === "overlay";
}

/** The served shell's pinned panels. */
export function pinnedPanels(): readonly PanelId[] {
  return shell ? (layoutStore.getState().prefs.pinned?.[shell.id] ?? []) : [];
}

/** The served shell's pinned panels, re-rendering on change. */
export function usePinnedPanels(): readonly PanelId[] {
  const id = shell?.id;
  return useStore(layoutStore, (s) => (id ? s.prefs.pinned?.[id] : undefined)) ?? NONE;
}
const NONE: readonly PanelId[] = [];

export function togglePin(id: PanelId): void {
  if (shell) layoutStore.getState().setPinned(shell.id, id, !pinnedPanels().includes(id));
}

/** The composed shell served; null for core's own UI. Set once at startup, before the frame renders. */
let shell: ClassicShell | null = null;

/**
 * Serve a composed shell: a document of its own per mode (context `<shell>` or
 * `<shell>/<mode>`), each starting from its arrangement's grid until the user
 * rearranges. `modeIds` are every mode the shell will switch to, plugins'
 * included. `null` serves core's own UI again.
 */
export function setClassicShell(next: ClassicShell | null, modeIds: readonly string[] = []): void {
  shell = next;
  shellContexts = ["default"];
  if (!next) return;
  const contexts = modeIds.length ? modeIds.map((m) => [contextOf(next.id, m), m] as const) : [[next.id, null] as const];
  shellContexts = contexts.map(([ctx]) => ctx);
  for (const [ctx, mode] of contexts) {
    const grid = arrangementOf(mode).grid;
    const version = next.version !== undefined ? {shellVersion: next.version} : {};
    layoutStore
      .getState()
      .setStartingDoc(CLASSIC_PRESET_ID, ctx, grid || next.version !== undefined ? () => ({...emptyLayoutDoc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx), ...(grid ? {grid} : {}), ...version}) : null);
  }
}

const contextOf = (shellId: string, mode: string | null) => (mode ? `${shellId}/${mode}` : shellId);

/** Every document context of the served UI: one per mode of a composed shell, else core's one. */
let shellContexts: string[] = ["default"];
const owner = () => shell?.id ?? CORE_PANEL_OWNER;
const docKey = (ctx: string) => layoutDocKey(CLASSIC_PRESET_ID, ctx);

/**
 * Back to the starting arrangement (the shell's grid for the mode): of the
 * active mode, or with `all`, of every mode. Panels stay open; where they show
 * starts over.
 */
export function resetArrangement(all = false): void {
  for (const ctx of all ? shellContexts : [classicContext()]) layoutStore.getState().removeDoc(CLASSIC_PRESET_ID, ctx);
}

/**
 * Bring the saved arrangements up to the shell's `version`: one made under
 * another goes through the shell's `migrate`, or starts over without it. Call
 * once the saved prefs are loaded.
 */
export function upgradeShellDocs(): void {
  const version = shell?.version;
  if (!shell || version === undefined) return;
  const store = layoutStore.getState();
  const docs: Record<string, LayoutDoc> = {};
  for (const ctx of shellContexts) {
    const raw = store.prefs.docs[docKey(ctx)];
    if (!raw || raw.shellVersion === version) continue;
    let next: LayoutDoc | null = null;
    try {
      next = shell.migrate ? shell.migrate(migrateLayoutDoc(raw, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx), raw.shellVersion) : null;
    } catch (err) {
      console.warn(`[layout] the shell's migrate threw for "${ctx}"; starting it over`, err);
    }
    if (next) docs[docKey(ctx)] = {...migrateLayoutDoc(next, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx), shellVersion: version};
    else store.removeDoc(CLASSIC_PRESET_ID, ctx);
  }
  if (Object.keys(docs).length) store.putDocs(docs);
}

/** Save every mode's arrangement under `name` (replacing one of that name). */
export function saveWorkspace(name: string): void {
  const docs = layoutStore.getState().prefs.docs;
  const saved: Record<string, LayoutDoc> = {};
  for (const ctx of shellContexts) {
    const d = docs[docKey(ctx)];
    if (d) saved[docKey(ctx)] = d;
  }
  layoutStore.getState().setWorkspace(owner(), name.trim(), saved);
}

/** Put a saved workspace's arrangements in place. False when there is none of that name. */
export function loadWorkspace(name: string): boolean {
  const ws = layoutStore.getState().prefs.workspaces?.[owner()]?.[name];
  if (!ws) return false;
  const docs: Record<string, LayoutDoc> = {};
  for (const ctx of shellContexts) {
    const d = ws[docKey(ctx)];
    if (d) docs[docKey(ctx)] = migrateLayoutDoc(d, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx);
  }
  layoutStore.getState().putDocs(docs);
  return true;
}

export function deleteWorkspace(name: string): void {
  layoutStore.getState().setWorkspace(owner(), name, null);
}

export function listWorkspaces(): string[] {
  return Object.keys(layoutStore.getState().prefs.workspaces?.[owner()] ?? {}).sort((a, b) => a.localeCompare(b));
}

/** The arrangement of `mode` (the active one by default): the mode's own over the shell's. */
function arrangementOf(mode: string | null = activeShellMode()): ClassicArrangement {
  if (!shell) return {};
  const own = (mode && shell.modes?.[mode]) || {};
  return {grid: own.grid ?? shell.grid, open: own.open ?? shell.open, docks: own.docks ?? shell.docks, cover: own.cover};
}

/** The panel the active mode draws over the 3D view, if any. */
export function classicCover(): PanelId | null {
  return arrangementOf().cover ?? null;
}

/** The document context the classic frame reads and writes: `"default"` for core's own UI. */
export function classicContext(): string {
  return shell ? contextOf(shell.id, activeShellMode()) : "default";
}

/** The context, re-rendering when the shell's mode changes. */
export function useClassicContext(): string {
  useSyncExternalStore(subscribeShellModes, shellModesVersion, shellModesVersion);
  return classicContext();
}

/**
 * Open the arrangement's starting panels: on every load, and on entering a mode.
 * Whether a panel is open is its own flag, which a reload resets, while where it
 * shows is the saved document. Call after the layout's panel controller is installed.
 */
export function openStartingPanels(reveal: (id: PanelId) => boolean): void {
  for (const id of arrangementOf().open ?? []) reveal(id);
}

/**
 * Leaving a mode: the panels its arrangement placed (in the grid or a window)
 * go with it, unless the mode entered places or opens them too. A panel open in
 * its home stays open. Call with the context left, after the switch.
 */
export function leaveContext(left: string, hide: (id: PanelId) => boolean, isOpen: (id: PanelId) => boolean): void {
  const before = layoutStore.getState().doc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, left);
  const now = classicDoc();
  const opening = new Set(arrangementOf().open ?? []);
  const pinned = new Set(pinnedPanels());
  const placedBefore = [...gridPanels(gridOf(before)), ...Object.keys(before.floats)];
  for (const id of placedBefore) {
    if (placeOf(now, id) !== "home") continue;
    if (pinned.has(id)) {
      carryPinned(before, id);
      continue;
    }
    if (opening.has(id) || !isOpen(id)) continue;
    hide(id);
  }
}

/**
 * A pinned panel the mode entered does not place: placed as it was, in a window
 * at the same rect, or in the group of the same id, else at the view's right edge.
 */
function carryPinned(before: LayoutDoc, id: PanelId): void {
  const f = before.floats[id];
  if (f) {
    updateClassic((d) => floatPanel(d, id, {x: f.x, y: f.y, w: f.w, h: f.h}));
    return;
  }
  const from = groupOf(gridOf(before), id);
  updateClassic((d) => {
    const into = from && findNode(gridOf(d), from.id);
    return dockInGridDoc(d, id, into && into.kind === "group" ? {node: from!.id, edge: "center"} : {node: VIEWPORT_NODE, edge: "right"});
  });
}

/** Where a dock id goes in the grid when the shell maps it to no group that exists: that edge of the view. */
const DOCK_EDGE: Readonly<Record<string, DockEdge>> = {left: "left", bottom: "bottom"};

/**
 * In a composed shell, place each plugin panel that names a dock, once (the
 * document's `known`): into the group the shell maps that dock to, else at that
 * edge of the view. After that it is the user's to move. Floating and overlay
 * docks are left to their region. Core's own UI places nothing this way.
 */
export function adoptDockedPanels(): void {
  if (!shell) return;
  const docks = arrangementOf().docks ?? {};
  // Per mode: a panel offered only in other modes is adopted when one of those is entered.
  // An unavailable one (a dormant or disabled plugin's) is placed when it becomes available.
  const candidates = listPanels().filter(
    (p) => p.owner !== CORE_PANEL_OWNER && dockOf(p) !== null && canDock(p) && offeredInMode(p) && isPanelAvailable(p, {}),
  );
  if (!candidates.length) return;
  updateClassic((d) => {
    const {doc, adopted} = adoptNewPanels(d, candidates.map((p) => p.id));
    let out = doc;
    for (const id of adopted) {
      const dock = dockOf(getPanel(id)!)!;
      if (dock === "float" || dock === "overlay") continue;
      const group = docks[dock];
      const target: GridTarget =
        group && findNode(gridOf(out), group) ? {node: group, edge: "center"} : {node: VIEWPORT_NODE, edge: DOCK_EDGE[dock] ?? "right"};
      out = dockInGridDoc(out, id, target);
    }
    return out;
  });
}

/** Plugin panels shown in a place of the layout's own (a group, a window) rather than their region. */
export function placedElsewhere(doc: LayoutDoc, customisable: boolean, narrow: boolean): PanelId[] {
  return listPanels()
    .filter((p) => p.owner !== CORE_PANEL_OWNER && (shownPlace(doc, p.id, customisable, narrow) !== "home" || isOverlayPanel(p)))
    .map((p) => p.id);
}

/** Core's top bar of toggles; a composed shell with a menu bar of its own may leave it out. */
let toolbarShown = true;

export function setClassicToolbar(on: boolean): void {
  toolbarShown = on;
}

export function classicToolbarShown(): boolean {
  return toolbarShown;
}

/** The user's classic document (of the active mode), re-rendering when it or the mode changes. */
export function useClassicDoc(): LayoutDoc {
  const ctx = useClassicContext();
  const raw = useStore(layoutStore, (s) => s.prefs.docs[layoutDocKey(CLASSIC_PRESET_ID, ctx)]);
  // Read through the store, so an unsaved document starts from the shell's grid.
  return useMemo(() => (raw ? migrateLayoutDoc(raw, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx) : classicDoc()), [raw, ctx]);
}

export function classicDoc(): LayoutDoc {
  return layoutStore.getState().doc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, classicContext());
}

export function updateClassic(fn: (doc: LayoutDoc) => LayoutDoc): void {
  layoutStore.getState().update(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, fn, classicContext());
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
  return panel.capabilities.dockable && !!panel.content && (homeOf(panel) !== null || dockOf(panel) !== null);
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
        if (!openPopout(panel.id, hide)) console.warn(`[layout] the browser blocked the window for "${panel.id}"`);
      },
    },
  ];
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
