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

import React, {Suspense, useEffect, useMemo, useRef, useState} from "react";

import CanvasWrapper from "@/components/viewer/CanvasWrapper";
import {SceneMenuHost} from "@/components/tree_view/SceneTreeMenu";
import NodeEditorComponent from "@/components/node_editor/NodeEditorComponent";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import {PanelFrame} from "@/components/ui/PanelFrame";
import {usePointerDrag} from "@/components/ui/FloatingWindow";
import ReloadIcon from "@/components/icons/ReloadIcon";
import {PluginPanelRegion, PluginTopBarButtons} from "@/plugins";
import {navBtnClass} from "@/components/navButton";
import {useNodeEditorStore} from "@/state/useNodeEditorStore";
import {request_list_of_nodes} from "@/utils/node_editor/handlers/request_list_of_nodes";
import {useIsNarrow} from "@/utils/useIsMobile";

import {CLASSIC_PANEL_CONTENTS, CLASSIC_PANEL_COMPONENTS} from "../classicBodies";
import {CORE_PANEL_OWNER, corePanelEnv, registerCorePanels} from "../corePanels";
import {FloatLayer} from "../FloatLayer";
import {DOCKED_PRESET_ID, DOCKED_ZONES, type LayoutDoc, type LayoutPreset, type ZoneDef} from "../layout";
import {LayoutHost, PanelMount, usePanelStates, useLayout} from "../LayoutHost";
import {layoutStore} from "../layoutStore";
import {localLayoutPersistence} from "../localPersistence";
import {placementFor, type PanelEnv, type PanelId, type PanelProps, type RegisteredPanel} from "../panel";
import type {PanelState} from "../panelStates";
import {PLUGIN_TOOLBAR_ORDER} from "../pluginPanels";
import {setZoneCollapsed, setZoneSize} from "../reducers";
import {listPanels, listToolbarPanels} from "../registry";
import {hide} from "../reveal";
import {CellBuilderOverlays, ToolbarButton, ViewerWindowChrome, useProceduralActive} from "./classic";
import {CLASSIC_TOOLBAR} from "./classicRules";
import {MinimisedTray, UndockedLayer, canFloat, columnActions, isUndocked, updateDoc, useLayoutDoc, type DocRef} from "./classicUndock";
import {
  DOCK_ZONE_DEFS,
  DOCKED_ZONE_DEFS,
  TOOLBAR_AREA,
  VIEWPORT_AREA,
  activateInZone,
  adoptDocked,
  dockGrid,
  dockZonePanels,
  dockedDefaults,
  shownTab,
  trackSize,
  zoneArea,
} from "./dockRules";

export const DOCKED_DOC: DocRef = {preset: DOCKED_PRESET_ID, zones: DOCKED_ZONE_DEFS, dockLabel: "Dock back into its zone"};

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
      actions={columnActions(panel, DOCKED_DOC)}
      onClose={panel.capabilities.closable ? close : undefined}
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

  return (
    <section
      aria-label={`${def.label} dock`}
      data-zone={def.id}
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
              <div role="tablist" aria-label={`${def.label} dock`} className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
                {own.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    role="tab"
                    id={`dock-tab-${p.id}`}
                    aria-selected={p.id === active}
                    aria-controls={`dock-panel-${p.id}`}
                    className={`shrink-0 rounded-sm px-2 py-1 text-xs ${
                      p.id === active ? "bg-gray-700 text-white" : "text-gray-400 hover:text-white hover:bg-white/10"
                    }`}
                    onClick={() => select(p.id)}
                  >
                    {p.title}
                  </button>
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
            {own.map((p) =>
              tabs ? (
                <div key={p.id} role="tabpanel" id={`dock-panel-${p.id}`} aria-labelledby={`dock-tab-${p.id}`} hidden={p.id !== active}>
                  <DockedPanel panel={p} zone={def} active={p.id === active} />
                </div>
              ) : (
                <DockedPanel key={p.id} panel={p} zone={def} active />
              ),
            )}
            {showsPluginRegion && <PluginPanelRegion region="top-panel" />}
          </div>
          {def.limits && <Splitter def={def} size={size} />}
        </>
      )}
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

  return (
    <div className="relative h-full w-full bg-gray-900 grid" style={dockGrid(tracks)} data-layout-preset={DOCKED_PRESET_ID}>
      <DockedToolbar />
      {DOCK_ZONE_DEFS.map((def) => (
        <DockZone key={def.id} def={def} doc={doc} panels={byZone.get(def.id) ?? []} states={states} mounted={mounted} hidden={narrow} />
      ))}

      {/* The viewport: one grid cell, rendered once; the docks only resize the tracks around it. */}
      <div style={{gridArea: VIEWPORT_AREA}} className="relative min-w-0 min-h-0 w-full h-full">
        {nodeEditorOnly ? <NodeEditorComponent /> : <CanvasWrapper />}
      </div>

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
  frame: () => Promise.resolve({default: DockFrame}),
};

/** The viewer in the docked preset. */
export default function DockedLayout() {
  registerCorePanels();
  const env = React.useMemo<PanelEnv>(() => corePanelEnv(), []);
  React.useEffect(() => {
    const start = async () => {
      if (!layoutStore.getState().hydrated) await layoutStore.getState().hydrate(localLayoutPersistence());
      // Panels registered since the document was saved are offered once.
      update((d) => adoptDocked(d, listPanels()));
    };
    void start();
  }, []);
  return <LayoutHost preset={dockedPreset} env={env} />;
}
