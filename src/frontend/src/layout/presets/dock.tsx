// The docked preset: the panels in docks around the viewport instead of over it.
//
// A CSS grid (`dockGrid`): the top bar across the top, the side docks full
// height -- left, left-aux | viewport | right-aux2, right-aux, right -- and the
// bottom dock under the viewport. A dock shows its open panels as tabs or as a
// stack (`ZoneDef.arrangement`), resizes by its splitter within its limits
// (`setZoneSize`), and collapses to a strip (`setZoneCollapsed`). A dock with no
// open panel takes no room.
//
// The viewport is one grid cell, rendered once; the docks only resize the
// tracks around it, so the WebGL canvas is never re-parented.
//
// Panels draw their frameless `content` in the shared `PanelFrame`. Core's
// panels keep their visibility flags: the document says where a panel docks
// (`./dockRules`), the flag says whether it shows. A panel may be floated out of
// its dock and minimised, through the same undock layer as the classic preset.
// The node editor, the attribute search and the admin / convert windows float
// as they do in the classic preset.
//
// Below `wide` there is no room for docks: the most recently opened panel takes
// the whole screen, as a floating window does there.
//
// The user rearranges the docks: a window dragged by its title bar, a tab
// dragged out of its strip or a stacked panel by its header drops into the zone
// under the pointer (`../dockDrag`) -- on a strip at that tab position, on a
// zone's body at its end; a tab dropped on no zone floats there. Every zone
// shows its target meanwhile, an empty one a temporary target over the
// viewport's edge. "Move to dock…" in a panel's header and Ctrl+Alt+arrow
// (`adjacentZone`) are the keyboard's way. All of it is the document's.

import React, {Suspense, useEffect, useMemo, useRef, useState} from "react";
import {createPortal} from "react-dom";
import {useStore} from "zustand";
import {createStore} from "zustand/vanilla";

import CanvasWrapper from "@/components/viewer/CanvasWrapper";
import {SceneMenuHost} from "@/components/tree_view/SceneTreeMenu";
import NodeEditorComponent from "@/components/node_editor/NodeEditorComponent";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import {MoveGlyph, PanelFrame} from "@/components/ui/PanelFrame";
import {usePointerDrag} from "@/components/ui/FloatingWindow";
import ReloadIcon from "@/components/icons/ReloadIcon";
import {PluginPanelRegion, PluginTopBarButtons} from "@/plugins";
import {navBtnClass} from "@/components/navButton";
import {useNodeEditorStore} from "@/state/useNodeEditorStore";
import {useViewerPanelStore} from "@/state/viewerPanelStore";
import {request_list_of_nodes} from "@/utils/node_editor/handlers/request_list_of_nodes";
import {useIsNarrow} from "@/utils/useIsMobile";

import {CLASSIC_PANEL_CONTENTS, CLASSIC_PANEL_COMPONENTS} from "../classicBodies";
import {CORE_PANEL_OWNER, corePanelEnv, registerCorePanels} from "../corePanels";
import {DROP_ATTR, DROP_AXIS_ATTR, DROP_INDEX_ATTR, dockDragStore, endDockDragAt, moveDockDrag, type DropTarget} from "../dockDrag";
import {FloatLayer} from "../FloatLayer";
import {DOCKED_PRESET_ID, DOCKED_ZONES, type LayoutDoc, type LayoutPreset, type ZoneDef} from "../layout";
import {LayoutHost, PanelMount, usePanelStates, useLayout} from "../LayoutHost";
import {layoutStore} from "../layoutStore";
import {viewerLayoutPersistence} from "../viewerPersistence";
import {placementFor, type PanelAction, type PanelEnv, type PanelId, type PanelProps, type RegisteredPanel, type ZoneId} from "../panel";
import type {PanelState} from "../panelStates";
import {PLUGIN_TOOLBAR_ORDER} from "../pluginPanels";
import {dockPanel, setZoneCollapsed, setZoneSize} from "../reducers";
import {getPanel, listPanels, listToolbarPanels} from "../registry";
import {hide} from "../reveal";
import {ZoneKeys, requestFloatFocus, restoreFocus} from "../zoneFocus";
import {PANEL_ACTIONS_ATTR, ZONE_ATTR, ZONE_PANEL_ATTR} from "../zoneKeys";
import {CellBuilderOverlays, ToolbarButton, ViewerWindowChrome, useProceduralActive} from "./classic";
import {CLASSIC_TOOLBAR} from "./classicRules";
import {
  MinimisedTray,
  UndockedLayer,
  canFloat,
  columnActions,
  dockColumnPanel,
  floatColumnPanel,
  isUndocked,
  updateDoc,
  useLayoutDoc,
  type DocRef,
} from "./classicUndock";
import {
  DOCK_ZONE_DEFS,
  DOCKED_ZONE_DEFS,
  TOOLBAR_AREA,
  VIEWPORT_AREA,
  activateInZone,
  adjacentZone,
  adoptDocked,
  dockGrid,
  dockInZone,
  dockZonePanels,
  dockedDefaults,
  dockedZoneOf,
  isDockZone,
  shownTab,
  trackSize,
  zoneArea,
  type DockDirection,
} from "./dockRules";

export const DOCKED_DOC: DocRef = {
  preset: DOCKED_PRESET_ID,
  zones: DOCKED_ZONE_DEFS,
  dockLabel: "Dock back into its zone",
  windowActions: (panel) => [moveAction(panel, "float")],
  drop: dropOnZone,
};

const update = (fn: (doc: LayoutDoc) => LayoutDoc) => updateDoc(DOCKED_DOC, fn);

/**
 * The zone's panels a document may list without opening any: the bound ones
 * (their flag opens them) and those it lists already.
 */
const listable = (doc: LayoutDoc, zone: string, panels: readonly RegisteredPanel[]) =>
  panels.filter((p) => p.binding || doc.zones[zone]?.panels.includes(p.id)).map((p) => p.id);

/** A panel shows in the docked preset while it is open and offered. */
const dockedMount = (_p: RegisteredPanel, s: PanelState) => s.open && s.available;

/** A plugin's top panel without a docked placement of its own is drawn by the plugin region host, not one by one. */
const viaPluginRegion = (p: RegisteredPanel) => p.owner !== CORE_PANEL_OWNER && !placementFor(p, DOCKED_PRESET_ID);

// ---------------------------------------------------------------------------
// Rearranging: moving a panel to another dock, a position in it, or a window.
// ---------------------------------------------------------------------------

/** The panels each dock shows, in order, as last drawn: a drop's position counts among these. */
const shownInZone = new Map<ZoneId, readonly PanelId[]>();

/** The panel a drop at `target` goes before; null at the end. The dragged panel itself does not count. */
function dropBefore(target: DropTarget, dragged: PanelId): PanelId | null {
  if (target.index === null) return null;
  return (shownInZone.get(target.zone) ?? []).slice(target.index).find((id) => id !== dragged) ?? null;
}

/** Focus the panel where it now is: its header's actions, else its tab. */
function focusPanel(id: PanelId): void {
  requestAnimationFrame(() => {
    const esc = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(id) : id;
    const tab = document.getElementById(`dock-tab-${id}`);
    if (tab && !document.querySelector(`[${PANEL_ACTIONS_ATTR}="${esc}"]`)) tab.focus();
    else restoreFocus(id, null);
  });
}

/** Dock `id` in `zone` before `before` (null: at the end). */
function moveToZone(id: PanelId, zone: ZoneId, before: PanelId | null = null): void {
  update((d) => dockInZone(d, zone, id, listable(d, zone, dockZonePanels(d, zone, listPanels())), before));
}

/** A window dropped at `target`: docked when it is a dock. */
function dropOnZone(id: PanelId, target: DropTarget | null): boolean {
  if (!target || !isDockZone(target.zone)) return false;
  moveToZone(id, target.zone, dropBefore(target, id));
  return true;
}

/**
 * Ctrl+Alt+arrow on a window or a docked panel: to the dock that way
 * (`adjacentZone`); a window with none that way (up) back into its own.
 */
function moveByKey(id: PanelId, dir: DockDirection): void {
  const panel = getPanel(id);
  if (!panel) return;
  let moved = false;
  update((d) => {
    const from = isUndocked(d, id) ? "float" : dockedZoneOf(d, panel);
    if (!from) return d;
    const to = adjacentZone(from, dir);
    const next = to ? dockInZone(d, to, id, listable(d, to, dockZonePanels(d, to, listPanels()))) : from === "float" ? dockPanel(d, id) : d;
    moved = next !== d;
    return next;
  });
  if (moved) focusPanel(id);
}

/** "Move to dock…": the docks, and a window; `where` the panel is now. */
function moveAction(panel: RegisteredPanel, where: ZoneId | "float"): PanelAction {
  return {
    id: "move",
    label: "Move to dock…",
    icon: MoveGlyph,
    run: () => {},
    menu: [
      ...DOCK_ZONE_DEFS.map((def) => ({
        id: def.id,
        label: def.label,
        current: where === def.id,
        disabled: where === def.id,
        run: () => {
          moveToZone(panel.id, def.id);
          focusPanel(panel.id);
        },
      })),
      {
        id: "float",
        label: "Float in a window",
        current: where === "float",
        disabled: where === "float" || !canFloat(panel),
        run: () => {
          requestFloatFocus(panel.id);
          floatColumnPanel(panel.id, DOCKED_DOC);
        },
      },
    ],
  };
}

/** Which panel a tab or header drag carries, and where the pointer is: drawn by `DragGhost`. */
const ghostStore = createStore<{title: string; x: number; y: number} | null>()(() => null);

/** The pointer travel that turns a press on a tab or a header into a drag. */
const DRAG_START = 5;
/** The class of a docked panel's header, which drags the panel out. */
const DOCK_HANDLE = "docked-panel-drag-handle";

/**
 * Dragging a docked panel by its tab or its header: past `DRAG_START` the drop
 * targets show and a ghost follows the pointer; the drop docks it where the
 * pointer is, or floats it there when on no zone. A press that never travelled
 * stays a click. `handle`: the press must start on the header, not a button.
 */
function usePanelDrag(panel: RegisteredPanel, handle = false) {
  const press = useRef<{x: number; y: number; on: boolean} | null>(null);
  const dragged = useRef(false);
  const reset = () => {
    press.current = null;
    ghostStore.setState(null, true);
  };
  return {
    onPointerDown: (e: React.PointerEvent) => {
      const t = e.target as Element;
      if (e.button !== 0 || (handle && (!t.closest?.(`.${DOCK_HANDLE}`) || t.closest("button")))) return;
      press.current = {x: e.clientX, y: e.clientY, on: false};
      dragged.current = false;
    },
    onPointerMove: (e: React.PointerEvent) => {
      const p = press.current;
      if (!p) return;
      if (!p.on) {
        if (Math.hypot(e.clientX - p.x, e.clientY - p.y) < DRAG_START) return;
        p.on = dragged.current = true;
        e.currentTarget.setPointerCapture?.(e.pointerId);
      }
      ghostStore.setState({title: panel.title, x: e.clientX, y: e.clientY}, true);
      moveDockDrag(panel.id, e.clientX, e.clientY);
    },
    onPointerUp: (e: React.PointerEvent) => {
      const on = press.current?.on;
      reset();
      if (!on) return;
      e.currentTarget.releasePointerCapture?.(e.pointerId);
      const target = endDockDragAt(e.clientX, e.clientY);
      if (target && isDockZone(target.zone)) moveToZone(panel.id, target.zone, dropBefore(target, panel.id));
      else if (!target && canFloat(panel)) floatColumnPanel(panel.id, DOCKED_DOC, {x: e.clientX, y: e.clientY});
    },
    onPointerCancel: () => {
      if (press.current?.on) dockDragStore.setState({panel: null, over: null, index: null});
      reset();
    },
    // The click that ends a drag is not a tab's selection.
    onClickCapture: (e: React.MouseEvent) => {
      if (!dragged.current) return;
      dragged.current = false;
      e.preventDefault();
      e.stopPropagation();
    },
  };
}

/** The dragged panel's title at the pointer. */
function DragGhost() {
  const g = useStore(ghostStore);
  if (!g) return null;
  return createPortal(
    <div
      aria-hidden="true"
      className="pointer-events-none fixed z-[80] rounded-sm border border-blue-400 bg-gray-800/90 px-2 py-1 text-xs text-white shadow-lg"
      // Left of the pointer near the right edge, so it stays on screen.
      style={g.x > window.innerWidth - 200 ? {right: window.innerWidth - g.x + 12, top: g.y + 12} : {left: g.x + 12, top: g.y + 12}}
    >
      {g.title}
    </div>,
    document.body,
  );
}

/** Where the pointer drops in `zone` while a drag is on: `"end"`, a position, or null when not over it. */
function useDropHover(zone: ZoneId): number | "end" | null {
  return useStore(dockDragStore, (s) => (s.over !== zone ? null : s.index === null ? "end" : s.index));
}

const dropAttrs = (zone: ZoneId, index?: number, axis?: "x" | "y") => ({
  [DROP_ATTR]: zone,
  ...(index !== undefined ? {[DROP_INDEX_ATTR]: String(index)} : {}),
  ...(axis ? {[DROP_AXIS_ATTR]: axis} : {}),
});

/** The insertion mark before (or, `after`, behind) a tab or a stacked panel. */
const InsertMark: React.FC<{axis: "x" | "y"; after?: boolean}> = ({axis, after}) => (
  <span
    aria-hidden="true"
    className={`pointer-events-none absolute z-10 bg-blue-400 ${
      axis === "x" ? `inset-y-0 w-0.5 ${after ? "right-0" : "left-0"}` : `inset-x-0 h-0.5 ${after ? "-bottom-1" : "-top-1"}`
    }`}
  />
);

/** A zone's outline while a drag is on, highlighted while the pointer is over it. */
const DropOutline: React.FC<{over: boolean}> = ({over}) => (
  <div
    aria-hidden="true"
    className={`pointer-events-none absolute inset-0 z-20 rounded-sm border-2 border-dashed ${
      over ? "border-blue-400 bg-blue-500/15" : "border-gray-400/60"
    }`}
  />
);

// ---------------------------------------------------------------------------
// The top bar.
// ---------------------------------------------------------------------------

function DockedToolbar() {
  const {env} = useLayout();
  const nodeEditorOnly = useNodeEditorStore((s) => s.use_node_editor_only);
  const panels = listToolbarPanels().filter((p) => p.owner === CORE_PANEL_OWNER && CLASSIC_TOOLBAR[p.id]);
  const states = usePanelStates(panels, env);
  const button = (p: RegisteredPanel) => <ToolbarButton key={p.id} panel={p} state={states.get(p.id)!} env={env} />;
  return (
    <div
      style={{gridArea: TOOLBAR_AREA}}
      {...{[ZONE_ATTR]: "toolbar"}}
      className="flex flex-row items-center gap-2 px-2 py-2 max-w-full overflow-x-auto bg-gray-900 border-b border-gray-700"
    >
      {nodeEditorOnly && (
        <button
          className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 rounded-sm transition-colors"}
          onClick={() => void request_list_of_nodes().catch((err) => console.error("Failed to reload nodes:", err))}
          title="Reload nodes"
        >
          <ReloadIcon />
        </button>
      )}
      {panels.filter((p) => p.toolbar!.order < PLUGIN_TOOLBAR_ORDER).map(button)}
      <PluginTopBarButtons navBtnClass={(active) => navBtnClass(active)} />
      {panels.filter((p) => p.toolbar!.order >= PLUGIN_TOOLBAR_ORDER).map(button)}
      <MinimisedTray docRef={DOCKED_DOC} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// One docked panel.
// ---------------------------------------------------------------------------

function DockedPanel({panel, zone, active}: {panel: RegisteredPanel; zone: ZoneDef; active: boolean}) {
  const close = () => void hide(panel.id);
  const props: PanelProps = {panelId: panel.id, host: {kind: "dock", zone: zone.id}, close, isActive: active};
  const body = panel.content ? (
    <PanelMount panel={panel} provided={CLASSIC_PANEL_CONTENTS[panel.id]} props={props} which="content" />
  ) : (
    // No frameless content: the panel's own card, as the classic column draws it.
    <PanelMount panel={panel} provided={CLASSIC_PANEL_COMPONENTS[panel.id]} props={props} />
  );
  const guarded = <ErrorBoundary label={panel.title}>{body}</ErrorBoundary>;
  if (!panel.content) return guarded;
  return (
    <PanelFrame
      panelId={panel.id}
      title={panel.title}
      host={props.host}
      actions={[...columnActions(panel, DOCKED_DOC), moveAction(panel, zone.id)]}
      onClose={panel.capabilities.closable ? close : undefined}
      dragHandleClassName={`${DOCK_HANDLE} cursor-grab select-none`}
    >
      {guarded}
    </PanelFrame>
  );
}

// ---------------------------------------------------------------------------
// A dock zone.
// ---------------------------------------------------------------------------

const KEY_STEP = 16;

function Splitter({def, size}: {def: ZoneDef; size: number}) {
  const lim = def.limits!;
  const sign = def.edge === "left" ? 1 : -1;
  const vertical = def.edge !== "bottom";
  const set = (s: number) => update((d) => setZoneSize(d, def.id, s, def));
  const drag = usePointerDrag<number>((start, dx, dy) => set(start + sign * (vertical ? dx : dy)));
  const edge =
    def.edge === "left" ? "top-0 -right-1 w-2 h-full cursor-ew-resize" : def.edge === "right" ? "top-0 -left-1 w-2 h-full cursor-ew-resize" : "left-0 -top-1 h-2 w-full cursor-ns-resize";
  return (
    <div
      role="separator"
      aria-orientation={vertical ? "vertical" : "horizontal"}
      aria-label={`Resize ${def.label.toLowerCase()} dock`}
      aria-valuemin={lim.min}
      aria-valuemax={lim.max}
      aria-valuenow={size}
      tabIndex={0}
      className={`group absolute z-10 touch-none ${edge}`}
      onPointerDown={(e) => drag.begin(e, size)}
      onPointerMove={drag.onPointerMove}
      onPointerUp={drag.onPointerUp}
      onPointerCancel={drag.onPointerCancel}
      onKeyDown={(e) => {
        const grow = vertical ? (def.edge === "left" ? "ArrowRight" : "ArrowLeft") : "ArrowUp";
        const shrink = vertical ? (def.edge === "left" ? "ArrowLeft" : "ArrowRight") : "ArrowDown";
        if (e.key === grow) set(size + KEY_STEP);
        else if (e.key === shrink) set(size - KEY_STEP);
        else return;
        e.preventDefault();
      }}
    >
      <span
        className={`absolute bg-transparent group-hover:bg-blue-400/70 group-focus-visible:bg-blue-400 ${
          vertical ? "inset-y-0 left-1/2 -translate-x-1/2 w-0.5" : "inset-x-0 top-1/2 -translate-y-1/2 h-0.5"
        }`}
      />
    </div>
  );
}

const ZONE_BORDER: Readonly<Record<string, string>> = {left: "border-r", right: "border-l", bottom: "border-t"};

const CollapseGlyph: React.FC<{edge: ZoneDef["edge"]; collapsed: boolean}> = ({edge, collapsed}) => {
  // Points the way the zone will move.
  const towardEdge = !collapsed;
  const dir = edge === "bottom" ? (towardEdge ? "down" : "up") : edge === "left" ? (towardEdge ? "left" : "right") : towardEdge ? "right" : "left";
  const d = {left: "M10 3 L5 8 L10 13", right: "M6 3 L11 8 L6 13", up: "M3 10 L8 5 L13 10", down: "M3 6 L8 11 L13 6"}[dir];
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d={d} stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
};

const ICON_BTN = "grid place-items-center h-7 w-7 shrink-0 rounded-sm text-gray-400 hover:text-white hover:bg-white/10";

/** A docked panel's tab, dragged out to move the panel. */
function DockTab({
  panel,
  zone,
  index,
  selected,
  dragging,
  mark,
  onSelect,
}: {
  panel: RegisteredPanel;
  zone: ZoneId;
  index: number;
  selected: boolean;
  dragging: boolean;
  /** The insertion mark the tab carries while a drop would land beside it. */
  mark: "before" | "after" | null;
  onSelect(): void;
}) {
  const drag = usePanelDrag(panel);
  return (
    <button
      type="button"
      role="tab"
      id={`dock-tab-${panel.id}`}
      aria-selected={selected}
      aria-controls={`dock-panel-${panel.id}`}
      {...{[ZONE_PANEL_ATTR]: panel.id}}
      {...(dragging ? dropAttrs(zone, index, "x") : {})}
      className={`relative shrink-0 select-none rounded-sm px-2 py-1 text-xs ${
        selected ? "bg-gray-700 text-white" : "text-gray-400 hover:text-white hover:bg-white/10"
      }`}
      onClick={onSelect}
      {...drag}
    >
      {panel.title}
      {mark && <InsertMark axis="x" after={mark === "after"} />}
    </button>
  );
}

/** A panel in a zone's body: its tab panel, or its place in the stack, dragged out by its header. */
function DockSlot({
  panel,
  zone,
  index,
  tabs,
  active,
  dragging,
  mark,
}: {
  panel: RegisteredPanel;
  zone: ZoneDef;
  index: number;
  tabs: boolean;
  active: boolean;
  dragging: boolean;
  mark: "before" | "after" | null;
}) {
  const drag = usePanelDrag(panel, true);
  const tab = tabs ? {role: "tabpanel", id: `dock-panel-${panel.id}`, "aria-labelledby": `dock-tab-${panel.id}`, hidden: !active} : {};
  return (
    <div
      {...tab}
      {...{[ZONE_PANEL_ATTR]: panel.id}}
      {...(dragging && !tabs ? dropAttrs(zone.id, index, "y") : {})}
      className="relative"
      {...drag}
    >
      <DockedPanel panel={panel} zone={zone} active={active} />
      {mark && <InsertMark axis="y" after={mark === "after"} />}
    </div>
  );
}

function DockZone({
  def,
  doc,
  panels,
  states,
  mounted,
  hidden,
}: {
  def: ZoneDef;
  doc: LayoutDoc;
  panels: readonly RegisteredPanel[];
  states: ReadonlyMap<PanelId, PanelState>;
  /** Panels kept mounted while closed (`keepAlive`, once opened). */
  mounted: ReadonlySet<PanelId>;
  hidden: boolean;
}) {
  const state = doc.zones[def.id];
  const shown = panels.filter((p) => !isUndocked(doc, p.id) && dockedMount(p, states.get(p.id)!));
  const own = shown.filter((p) => !viaPluginRegion(p));
  const open = own.map((p) => p.id);
  const tabs = def.arrangement === "tabs";
  const active = tabs ? shownTab(state, open) : null;
  const collapsed = state?.collapsed === true;
  const size = trackSize(def, state, true);
  const showsPluginRegion = def.id === DOCKED_ZONES.rightAux;
  const empty = shown.length === 0;
  const setCollapsed = (c: boolean) => update((d) => setZoneCollapsed(d, def.id, c));
  const select = (id: PanelId) => update((d) => activateInZone(setZoneCollapsed(d, def.id, false), def.id, id, listable(d, def.id, panels)));

  const keep = panels.filter((p) => !open.includes(p.id) && mounted.has(p.id) && !isUndocked(doc, p.id));
  const vertical = def.edge !== "bottom";
  shownInZone.set(def.id, open);
  const dragging = useStore(dockDragStore, (s) => s.panel !== null) && !hidden && !empty;
  const hover = useDropHover(def.id);
  /** The insertion mark at position `i` of `n`: before item `i`, or behind the last one for the end. */
  const markAt = (i: number, n: number): "before" | "after" | null =>
    typeof hover !== "number" ? null : hover === i ? "before" : hover >= n && i === n - 1 ? "after" : null;

  return (
    <section
      aria-label={`${def.label} dock`}
      data-zone={def.id}
      {...{[ZONE_ATTR]: "dock"}}
      {...(dragging ? dropAttrs(def.id) : {})}
      hidden={hidden || empty}
      style={{gridArea: zoneArea(def.id)}}
      className={`relative min-w-0 min-h-0 ${hidden || empty ? "hidden" : "flex"} flex-col bg-gray-800 text-white text-sm border-gray-700 ${ZONE_BORDER[def.edge ?? "left"]}`}
    >
      {collapsed ? (
        <div className={`flex ${vertical ? "flex-col" : "flex-row"} items-center gap-1 p-0.5`}>
          <button type="button" className={ICON_BTN} onClick={() => setCollapsed(false)} title={`Expand the ${def.label.toLowerCase()} dock`} aria-label={`Expand the ${def.label.toLowerCase()} dock`}>
            <CollapseGlyph edge={def.edge} collapsed />
          </button>
          {own.map((p) => (
            <button key={p.id} type="button" className={ICON_BTN} onClick={() => select(p.id)} title={p.title} aria-label={p.title}>
              {p.icon ? <p.icon /> : <span className="text-xs">{p.title.slice(0, 2)}</span>}
            </button>
          ))}
        </div>
      ) : (
        <>
          <div className="flex items-center gap-1 h-9 shrink-0 px-1 border-b border-gray-700">
            {tabs ? (
              <div
                role="tablist"
                aria-label={`${def.label} dock`}
                {...(dragging ? dropAttrs(def.id, own.length) : {})}
                className="flex min-w-0 flex-1 self-stretch items-center gap-0.5 overflow-x-auto"
              >
                {own.map((p, i) => (
                  <DockTab
                    key={p.id}
                    panel={p}
                    zone={def.id}
                    index={i}
                    selected={p.id === active}
                    dragging={dragging}
                    mark={markAt(i, own.length)}
                    onSelect={() => select(p.id)}
                  />
                ))}
              </div>
            ) : (
              <span className="flex-1 truncate px-1 text-xs uppercase tracking-wide text-gray-400">{def.label}</span>
            )}
            <button type="button" className={ICON_BTN} onClick={() => setCollapsed(true)} title={`Collapse the ${def.label.toLowerCase()} dock`} aria-label={`Collapse the ${def.label.toLowerCase()} dock`}>
              <CollapseGlyph edge={def.edge} collapsed={false} />
            </button>
          </div>
          <div className={`flex-1 min-h-0 overflow-y-auto p-2 ${tabs ? "" : "flex flex-col gap-2"}`}>
            {own.map((p, i) => (
              <DockSlot
                key={p.id}
                panel={p}
                zone={def}
                index={i}
                tabs={tabs}
                active={tabs ? p.id === active : true}
                dragging={dragging}
                mark={tabs ? null : markAt(i, own.length)}
              />
            ))}
            {showsPluginRegion && <PluginPanelRegion region="top-panel" />}
          </div>
          {def.limits && <Splitter def={def} size={size} />}
        </>
      )}
      {dragging && <DropOutline over={hover !== null} />}
      {/* Closed panels kept alive stay mounted out of sight. */}
      {keep.length > 0 && (
        <div hidden>
          {keep.map((p) => (
            <DockedPanel key={p.id} panel={p} zone={def} active={false} />
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// The frame.
// ---------------------------------------------------------------------------

/** The panel of each dock zone, by zone. Registration order breaks ties. */
function useDockPanels(doc: LayoutDoc): {byZone: Map<string, RegisteredPanel[]>; all: RegisteredPanel[]} {
  const registered = listPanels();
  return useMemo(() => {
    const byZone = new Map<string, RegisteredPanel[]>();
    for (const def of DOCK_ZONE_DEFS) byZone.set(def.id, dockZonePanels(doc, def.id, registered));
    return {byZone, all: [...byZone.values()].flat()};
    // The registry only grows; its size identifies it.
  }, [doc, registered.length]);
}

/**
 * Follows which panels open: a panel that opens becomes its zone's shown tab
 * (and uncollapses the zone), and is remembered for the narrow takeover and for
 * `keepAlive`.
 */
function useOpenTracking(doc: LayoutDoc, byZone: Map<string, RegisteredPanel[]>, states: ReadonlyMap<PanelId, PanelState>) {
  const prev = useRef<Set<PanelId> | null>(null);
  const [recent, setRecent] = useState<PanelId[]>([]);
  const [mounted, setMounted] = useState<ReadonlySet<PanelId>>(() => new Set());
  const openNow = new Set<PanelId>();
  for (const panels of byZone.values()) for (const p of panels) if (dockedMount(p, states.get(p.id)!)) openNow.add(p.id);
  const key = [...openNow].sort().join(",");
  useEffect(() => {
    const before = prev.current;
    prev.current = openNow;
    const opened = [...openNow].filter((id) => !before?.has(id));
    if (opened.length === 0 && before && before.size === openNow.size) return;
    setRecent((r) => [...r.filter((id) => openNow.has(id) && !opened.includes(id)), ...opened]);
    const keepAlive = [...byZone.values()].flat().filter((p) => p.capabilities.keepAlive && openNow.has(p.id));
    if (keepAlive.length) setMounted((m) => new Set([...m, ...keepAlive.map((p) => p.id)]));
    // On first render every open panel is "opened"; the saved tabs stand.
    if (!before) return;
    for (const [zone, panels] of byZone) {
      const def = DOCK_ZONE_DEFS.find((z) => z.id === zone)!;
      for (const id of opened) {
        if (!panels.some((p) => p.id === id)) continue;
        update((d) => {
          let next = d.zones[zone]?.collapsed ? setZoneCollapsed(d, zone, false) : d;
          if (def.arrangement === "tabs") next = activateInZone(next, zone, id, listable(next, zone, panels));
          return next;
        });
      }
    }
    // `openNow` is identified by `key`.
  }, [key]);
  return {recent, mounted};
}

/** The width (height for the bottom) of an empty dock's temporary target, in px. */
const EMPTY_TARGET = 96;

/**
 * While a drag is on: a temporary target for each dock that shows no panel,
 * along the viewport's edges in the docks' order (outermost at the edge). The
 * grid does not change, so nothing moves under the pointer.
 */
function EmptyDockTargets({empty}: {empty: readonly ZoneDef[]}) {
  const dragging = useStore(dockDragStore, (s) => s.panel !== null);
  const over = useStore(dockDragStore, (s) => s.over);
  if (!dragging || empty.length === 0) return null;
  const left = empty.filter((z) => z.edge === "left");
  const right = empty.filter((z) => z.edge === "right").reverse();
  const bottom = empty.find((z) => z.edge === "bottom");
  const target = (def: ZoneDef, style: React.CSSProperties) => (
    <div
      key={def.id}
      {...dropAttrs(def.id)}
      style={style}
      className={`pointer-events-auto absolute flex items-center justify-center rounded-md border-2 border-dashed p-1 text-center text-xs ${
        over === def.id ? "border-blue-400 bg-blue-500/30 text-blue-100" : "border-gray-400/70 bg-gray-900/60 text-gray-300"
      }`}
    >
      {def.label}
    </div>
  );
  const sideBottom = bottom ? EMPTY_TARGET + 8 : 4;
  return (
    <div style={{gridArea: VIEWPORT_AREA}} className="pointer-events-none relative z-10 min-w-0 min-h-0" aria-hidden="true">
      {left.map((def, i) => target(def, {left: 4 + i * (EMPTY_TARGET + 4), top: 4, bottom: sideBottom, width: EMPTY_TARGET}))}
      {right.map((def, i) => target(def, {right: 4 + i * (EMPTY_TARGET + 4), top: 4, bottom: sideBottom, width: EMPTY_TARGET}))}
      {bottom &&
        target(bottom, {
          left: 4 + left.length * (EMPTY_TARGET + 4),
          right: 4 + right.length * (EMPTY_TARGET + 4),
          bottom: 4,
          height: EMPTY_TARGET,
        })}
    </div>
  );
}

const closeFloat = (id: PanelId) => void hide(id);
const dockFloat = (id: PanelId) => dockColumnPanel(id, DOCKED_DOC);

/** Below `wide`: the most recently opened docked panel, full screen. */
function NarrowTakeover({panel}: {panel: RegisteredPanel}) {
  const close = () => void hide(panel.id);
  const host = {kind: "window" as const, zone: "docked.sheet"};
  const props: PanelProps = {panelId: panel.id, host, close, isActive: true};
  return (
    <div className="fixed inset-0 z-[45]">
      <PanelFrame panelId={panel.id} title={panel.title} host={host} actions={[]} onClose={close}>
        <div className="h-full overflow-y-auto p-2 text-sm text-[var(--ada-panel-text)]">
          <ErrorBoundary label={panel.title}>
            <PanelMount panel={panel} provided={(panel.content ? CLASSIC_PANEL_CONTENTS : CLASSIC_PANEL_COMPONENTS)[panel.id]} props={props} which={panel.content ? "content" : "body"} outerSuspense />
          </ErrorBoundary>
        </div>
      </PanelFrame>
    </div>
  );
}

function DockFrame() {
  const {env} = useLayout();
  const doc = useLayoutDoc(DOCKED_DOC);
  const nodeEditorOnly = useNodeEditorStore((s) => s.use_node_editor_only);
  const proceduralActive = useProceduralActive();
  const narrow = useIsNarrow();
  const {byZone, all} = useDockPanels(doc);
  const states = usePanelStates(all, env);
  const {recent, mounted} = useOpenTracking(doc, byZone, states);

  const tracks: Record<string, number> = {};
  for (const def of DOCK_ZONE_DEFS) {
    const shows = !narrow && (byZone.get(def.id) ?? []).some((p) => !isUndocked(doc, p.id) && dockedMount(p, states.get(p.id)!));
    tracks[def.id] = trackSize(def, doc.zones[def.id], shows);
  }
  const takeover = narrow
    ? [...recent].reverse().map((id) => all.find((p) => p.id === id)).find((p) => p && !viaPluginRegion(p) && !isUndocked(doc, p.id) && dockedMount(p, states.get(p.id)!))
    : undefined;
  const floatable = all.filter((p) => canFloat(p));
  const empty = narrow ? [] : DOCK_ZONE_DEFS.filter((def) => tracks[def.id] === 0);
  // The admin and convert windows may both be open.
  useEffect(() => useViewerPanelStore.getState().setExclusive(false), []);

  return (
    <div className="relative h-full w-full bg-gray-900 grid" style={dockGrid(tracks)} data-layout-preset={DOCKED_PRESET_ID}>
      <DockedToolbar />
      {DOCK_ZONE_DEFS.map((def) => (
        <DockZone key={def.id} def={def} doc={doc} panels={byZone.get(def.id) ?? []} states={states} mounted={mounted} hidden={narrow} />
      ))}

      {/* The viewport: one grid cell, rendered once; the docks only resize the tracks around it. */}
      <div style={{gridArea: VIEWPORT_AREA}} className="relative min-w-0 min-h-0 w-full h-full" {...{[ZONE_ATTR]: "viewport"}} tabIndex={-1}>
        {nodeEditorOnly ? <NodeEditorComponent /> : <CanvasWrapper />}
      </div>
      <EmptyDockTargets empty={empty} />
      <ZoneKeys closeFloat={closeFloat} dockFloat={dockFloat} movePanel={moveByKey} />
      <DragGhost />

      {/* The Scene's right-click menu; portals to the body. */}
      <SceneMenuHost />
      {proceduralActive && <CellBuilderOverlays />}

      <FloatLayer zone={DOCKED_ZONES.float} mount={dockedMount} provided={CLASSIC_PANEL_COMPONENTS} />
      <UndockedLayer panels={floatable} mounts={dockedMount} contents={CLASSIC_PANEL_CONTENTS} docRef={DOCKED_DOC} />
      {takeover && <NarrowTakeover key={takeover.id} panel={takeover} />}

      {env.restMode === true && (
        <Suspense fallback={null}>
          <RestModeUI />
        </Suspense>
      )}

      <FloatLayer zone={DOCKED_ZONES.window} mount={dockedMount} chromeFor={() => ViewerWindowChrome} provided={CLASSIC_PANEL_COMPONENTS} />
    </div>
  );
}

const RestModeUI = React.lazy(() => import("@/components/rest_mode/RestModeUI"));

export const dockedPreset: LayoutPreset = {
  id: DOCKED_PRESET_ID,
  label: "Docked",
  description: "Panels in docks around the viewport: tabs or stacks at the sides and the bottom, resizable and collapsible.",
  owner: CORE_PANEL_OWNER,
  zones: [...DOCKED_ZONE_DEFS],
  defaults: dockedDefaults,
  // The admin and convert windows may float side by side.
  exclusiveGroups: () => false,
  frame: () => Promise.resolve({default: DockFrame}),
};

/** The viewer in the docked preset. */
export default function DockedLayout() {
  registerCorePanels();
  const env = React.useMemo<PanelEnv>(() => corePanelEnv(), []);
  React.useEffect(() => {
    const start = async () => {
      if (!layoutStore.getState().hydrated) await layoutStore.getState().hydrate(viewerLayoutPersistence());
      // Panels registered since the document was saved are offered once.
      update((d) => adoptDocked(d, listPanels()));
    };
    void start();
  }, []);
  return <LayoutHost preset={dockedPreset} env={env} />;
}
