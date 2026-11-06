// The classic preset: the viewer's own UI, as a layout of registered panels.
//
// Rendered through `LayoutHost`, the only path the UI takes. It replaced a
// hand-written tree (since removed) and kept its DOM: the frame below emits
// the same wrapper elements with the same classes, and the zones are filled
// from the registry:
//
//   classic.tree         the selection tree, always mounted (it hides itself)
//   (toolbar)            one toggle per core toolbar panel, in `toolbar.order`,
//                        drawn from `CLASSIC_TOOLBAR`, plus the plugin buttons
//                        at their slot -- a gated toggle stays in the row,
//                        `hidden`, a hosted-only one is omitted
//   classic.menu-column  the stacked panels, in placement order, each its own
//                        card (`frame: "own"`), with the plugin `top-panel`
//                        region at its slot
//   classic.float        the node editor and the attribute search
//   classic.window       the in-viewer admin / convert window
//
// Visibility is each panel's binding, and the mount rules (`classicRules`)
// say where mounting differs from "open". Panel components come synchronously
// from the classic shell's own module graph (`../classicBodies`), and lazily,
// behind Suspense, where they are code-split.

import React, {Suspense} from "react";

import CanvasWrapper from "@/components/viewer/CanvasWrapper";
import {SceneMenuHost} from "@/components/tree_view/SceneTreeMenu";
import NodeEditorComponent from "@/components/node_editor/NodeEditorComponent";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import {CellBuilderOverlays} from "../CellBuilderOverlays";
import {WebsocketStatusMenu} from "@/components/WebsocketStatusMenu";
import {ViewerWindow, VIEWER_PANEL_TITLE, viewerPanelExternal} from "@/components/ViewerWindow";
import {navBtnClass} from "@/components/navButton";
import ReloadIcon from "@/components/icons/ReloadIcon";
import {PluginPanelRegion, PluginTopBarButtons} from "@/plugins";
import {hasEmbeddedDoc, useCellBuilderStore} from "@/state/cellBuilderStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {useNodeEditorStore} from "@/state/useNodeEditorStore";
import {useViewerPanelStore, type ViewerPanel} from "@/state/viewerPanelStore";
import {request_list_of_nodes} from "@/utils/node_editor/handlers/request_list_of_nodes";
import {useIsWide} from "@/utils/useIsMobile";

import {PanelHeaderActionsProvider} from "@/components/ui/PanelFrame";
import {panelBodyAttrsFor} from "@/components/ui/panelChrome";

import {CLASSIC_PANEL_COMPONENTS, CLASSIC_PANEL_CONTENTS} from "../classicBodies";
import {CORE_PANEL_IDS, CORE_PANEL_OWNER, corePanelEnv, registerCorePanels} from "../corePanels";
import {FloatLayer, type WindowChrome} from "../FloatLayer";
import {CLASSIC_PRESET_ID, CLASSIC_ZONES, type LayoutDoc, type LayoutPreset} from "../layout";
import {customisablePanels} from "../customisable";
import {LayoutHost, PanelMount, usePanelStates, useLayout, zonePanels} from "../LayoutHost";
import {layoutStore} from "../layoutStore";
import {viewerLayoutPersistence} from "../viewerPersistence";
import type {PanelAction, PanelEnv, PanelId, PanelProps, RegisteredPanel} from "../panel";
import type {PanelState} from "../panelStates";
import {PLUGIN_MENU_COLUMN_ORDER, PLUGIN_TOOLBAR_ORDER} from "../pluginPanels";
import {getPanel, listPanels, listToolbarPanels} from "../registry";
import {hide, isPanelOpen, reveal, toggle} from "../reveal";
import {CLASSIC_TOOLBAR, CLASSIC_ZONE_DEFS, classicDefaults, classicMounts, toolbarPresence} from "./classicRules";
import {PANEL_TOGGLE_ATTR, ZONE_ATTR} from "../zoneKeys";
import {endDockDragAt, moveDockDrag} from "../dockDrag";
import {gridOf} from "../reducers";
import {gridPanels, shownGrid, type Rect} from "../splitTree";
import {useIsNarrow} from "@/utils/useIsMobile";
import type {WindowDock} from "@/components/ui/PanelFrame";
import {ClassicZoneKeys, ColumnDropTarget, MinimisedTray, UndockedLayer, columnActions} from "./classicUndock";
import {GridView, ViewportDropTarget} from "./classicGrid";
import {setPlacedElsewhere} from "../placedElsewhere";
import {installMainWindow, windowBus} from "../windows";
import {hostMirror} from "../windowMirror";
import {registerCoreMirror} from "../windowMirrorCore";
import {ShortcutKeys} from "../ShortcutKeys";
import {ViewportOverlays} from "../ViewportOverlayLayer";
import {offeredInMode} from "../shellModes";
import {
  adoptDockedPanels,
  canDock,
  canFloat,
  classicCover,
  classicOverlays,
  classicShellId,
  classicToastAnchor,
  isOverlayPanel,
  leaveContext,
  upgradeShellDocs,
  placedElsewhere,
  useClassicContext,
  classicToolbarShown,
  dropPanel,
  openStartingPanels,
  moveAction,
  shownPlace,
  useClassicDoc,
  useCustomisablePanels,
} from "./classicPlace";

const RestModeUI = React.lazy(() => import("@/components/rest_mode/RestModeUI"));

export function useProceduralActive(): boolean {
  return useCellBuilderStore((s) => s.active !== null || hasEmbeddedDoc(s));
}

const mounts = classicMounts;

// ---------------------------------------------------------------------------
// The top bar.
// ---------------------------------------------------------------------------

export function ToolbarButton({
  panel,
  state,
  env,
  customisable = false,
}: {
  panel: RegisteredPanel;
  state: PanelState;
  env: PanelEnv;
  customisable?: boolean;
}) {
  const recipe = CLASSIC_TOOLBAR[panel.id];
  if (toolbarPresence(panel, state, env) === "omit") return null;
  if (recipe.statusMenu) {
    return (
      <div className={navBtnClass(state.open)}>
        <WebsocketStatusMenu />
      </div>
    );
  }
  return (
    <button
      className={navBtnClass(state.open, "", !state.available)}
      hidden={!state.available}
      onClick={() => void toggle(panel.id)}
      title={recipe.title(state.open)}
      aria-label={recipe.ariaLabel?.(state.open)}
      aria-pressed={state.open}
      {...(customisable ? {[PANEL_TOGGLE_ATTR]: panel.id} : {})}
    >
      {recipe.content}
    </button>
  );
}

function ClassicToolbar({nodeEditorOnly}: {nodeEditorOnly: boolean}) {
  const {env} = useLayout();
  const customisable = useCustomisablePanels();
  // Core's buttons; plugin buttons are drawn by their own host at their slot,
  // where activation predicates (which need a plugin context) are evaluated.
  const panels = listToolbarPanels().filter((p) => p.owner === CORE_PANEL_OWNER);
  const states = usePanelStates(panels, env);
  const before = panels.filter((p) => p.toolbar!.order < PLUGIN_TOOLBAR_ORDER);
  const after = panels.filter((p) => p.toolbar!.order >= PLUGIN_TOOLBAR_ORDER);
  const button = (p: RegisteredPanel) => <ToolbarButton key={p.id} panel={p} state={states.get(p.id)!} env={env} customisable={customisable} />;
  return (
    <div
      className={"flex flex-row items-center gap-2 px-2 max-w-full overflow-x-auto pointer-events-auto"}
      {...(customisable ? {[ZONE_ATTR]: "toolbar"} : {})}
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
      {before.map(button)}
      <PluginTopBarButtons navBtnClass={(active) => navBtnClass(active)} />
      {after.map(button)}
      {/* No palette button: Ctrl+K opens it, and the shortcuts dialog lists it. */}
      {customisable && <MinimisedTray />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The menu column.
// ---------------------------------------------------------------------------

/** The selected-object box restarts after a crash when the selection changes. */
const ObjectInfoBoundary: React.FC<{children: React.ReactNode}> = ({children}) => {
  const infoName = useObjectInfoStore((s) => s.name);
  const cbSelectionKey = useCellBuilderStore((s) => s.selection?.cellId ?? null);
  return (
    <ErrorBoundary label="Selected object info" resetKeys={[infoName, cbSelectionKey]}>
      {children}
    </ErrorBoundary>
  );
};

const SceneBoundary: React.FC<{children: React.ReactNode}> = ({children}) => (
  <ErrorBoundary label="Scene info">{children}</ErrorBoundary>
);

/** Error boundaries around column panels. */
const COLUMN_WRAP: Readonly<Record<PanelId, React.ComponentType<{children: React.ReactNode}>>> = {
  [CORE_PANEL_IDS.objectInfo]: ObjectInfoBoundary,
  [CORE_PANEL_IDS.scene]: SceneBoundary,
};

export {CellBuilderOverlays};

const NO_ACTIONS: readonly PanelAction[] = [];

function ColumnPanel({
  panel,
  state,
  env,
  proceduralActive,
  customisable,
  doc,
}: {
  panel: RegisteredPanel;
  state: PanelState;
  env: PanelEnv;
  proceduralActive: boolean;
  customisable: boolean;
  doc: LayoutDoc;
}) {
  if (!mounts(panel.id, {state, env, proceduralActive})) return null;
  const props: PanelProps = {
    panelId: panel.id,
    host: {kind: "stack", zone: CLASSIC_ZONES.menuColumn},
    close: () => void hide(panel.id),
    isActive: true,
  };
  const mount = <PanelMount panel={panel} provided={CLASSIC_PANEL_COMPONENTS[panel.id]} props={props} />;
  const Wrap = COLUMN_WRAP[panel.id];
  const wrapped = Wrap ? <Wrap>{mount}</Wrap> : mount;
  // Always provided (it draws no DOM), so turning the setting on or off never remounts a panel.
  return (
    <PanelHeaderActionsProvider
      actions={customisable ? columnActions(panel, doc) : NO_ACTIONS}
      panelId={customisable ? panel.id : undefined}
    >
      {wrapped}
    </PanelHeaderActionsProvider>
  );
}

function ClassicColumn({proceduralActive}: {proceduralActive: boolean}) {
  const {preset, env} = useLayout();
  const customisable = useCustomisablePanels();
  const doc = useClassicDoc();
  const narrow = useIsNarrow();
  // Core's panels; the plugin `top-panel` region host draws the plugins' at their slot.
  const panels = zonePanels(preset, CLASSIC_ZONES.menuColumn).filter((p) => p.owner === CORE_PANEL_OWNER);
  const states = usePanelStates(panels, env);
  const render = (p: RegisteredPanel) => (
    <React.Fragment key={p.id}>
      {shownPlace(doc, p.id, customisable, narrow) === "home" && (
        <ColumnPanel
          panel={p}
          state={states.get(p.id)!}
          env={env}
          proceduralActive={proceduralActive}
          customisable={customisable}
          doc={doc}
        />
      )}
      {p.id === CORE_PANEL_IDS.cellbuilder && proceduralActive && <CellBuilderOverlays />}
    </React.Fragment>
  );
  const order = (p: RegisteredPanel) => p.placement?.[CLASSIC_PRESET_ID]?.order ?? 0;
  return (
    <div
      className={"px-2 gap-2 flex flex-col pointer-events-auto max-w-[100vw]"}
      {...(customisable ? {[ZONE_ATTR]: "column"} : {})}
    >
      {panels.filter((p) => order(p) < PLUGIN_MENU_COLUMN_ORDER).map(render)}
      <PluginPanelRegion region="top-panel" />
      {panels.filter((p) => order(p) >= PLUGIN_MENU_COLUMN_ORDER).map(render)}
    </div>
  );
}

/**
 * The top bar over the panel column, pushed right by the open tree on wide
 * screens, and over the viewport where the split grid has groups left of or
 * above it.
 */
function ClassicMenu({viewport}: {viewport: Rect | null}) {
  const nodeEditorOnly = useNodeEditorStore((s) => s.use_node_editor_only);
  const isTreeCollapsed = useTreeViewStore((s) => s.isTreeCollapsed);
  const treeViewWidth = useTreeViewStore((s) => s.treeViewWidth);
  const isWide = useIsWide();
  const proceduralActive = useProceduralActive();
  const doc = useClassicDoc();
  const customisable = useCustomisablePanels();
  const treeHome = shownPlace(doc, CORE_PANEL_IDS.tree, customisable, !isWide) === "home";
  // The tree is a push track on desktop; on phones it overlays, as there is no
  // horizontal room to give up.
  const menuShiftPx = Math.max(!isTreeCollapsed && isWide && treeHome ? treeViewWidth : 0, Math.round(viewport?.left ?? 0));
  const top = Math.round(viewport?.top ?? 0);
  return (
    <div className="relative w-full h-full">
      <div
        className="absolute left-0 top-0 z-10 py-2 gap-2 flex flex-col pointer-events-none transition-[padding] duration-150"
        style={top > 0 ? {paddingLeft: `${menuShiftPx}px`, paddingTop: `${top + 8}px`} : {paddingLeft: `${menuShiftPx}px`}}
      >
        {classicToolbarShown() && <ClassicToolbar nodeEditorOnly={!!nodeEditorOnly} />}
        <ClassicColumn proceduralActive={proceduralActive} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Zones around the viewport.
// ---------------------------------------------------------------------------

function TreeZone() {
  const {preset, env} = useLayout();
  const customisable = useCustomisablePanels();
  const doc = useClassicDoc();
  const narrow = useIsNarrow();
  const panels = zonePanels(preset, CLASSIC_ZONES.tree);
  const states = usePanelStates(panels, env);
  return (
    <>
      {panels.map((p) =>
        mounts(p.id, {state: states.get(p.id)!, env, proceduralActive: false}) && shownPlace(doc, p.id, customisable, narrow) === "home" ? (
          <PanelHeaderActionsProvider key={p.id} actions={customisable ? columnActions(p, doc) : NO_ACTIONS} panelId={customisable ? p.id : undefined}>
            <PanelMount
              panel={p}
              provided={CLASSIC_PANEL_COMPONENTS[p.id]}
              props={{panelId: p.id, host: {kind: "dock", zone: CLASSIC_ZONES.tree}, close: () => void hide(p.id), isActive: true}}
            />
          </PanelHeaderActionsProvider>
        ) : null,
      )}
    </>
  );
}

const VIEWER_PANEL_OF: Readonly<Record<PanelId, ViewerPanel>> = {
  [CORE_PANEL_IDS.admin]: "admin",
  [CORE_PANEL_IDS.convert]: "convert",
};

/** The in-viewer window's own chrome: its title bar and "open in a dedicated tab". */
export const ViewerWindowChrome: WindowChrome = ({panel, close, children}) => {
  const adminTab = useViewerPanelStore((s) => s.adminTab);
  const which = VIEWER_PANEL_OF[panel.id];
  const shown = useViewerPanelStore((s) => s.shown);
  const raise = useViewerPanelStore((s) => s.raisePanel);
  // Front last: a window's place in `shown` is its layer, and a press brings it to the front.
  const index = which ? Math.max(0, shown.indexOf(which)) : 0;
  const first = React.useRef(index);
  return (
    <ViewerWindow
      // A second window opens stepped down from the first, not on top of it.
      offset={first.current * 32}
      layer={index}
      onActivate={which ? () => raise(which) : undefined}
      title={which ? VIEWER_PANEL_TITLE[which] : panel.title}
      onExternal={which ? viewerPanelExternal(which, adminTab, close) : null}
      onClose={close}
    >
      {children}
    </ViewerWindow>
  );
};

function useMountRule(): (panel: RegisteredPanel, state: PanelState, env: PanelEnv) => boolean {
  const proceduralActive = useProceduralActive();
  return (panel, state, env) => mounts(panel.id, {state, env, proceduralActive});
}

const sameRect = (a: Rect | null, b: Rect | null) =>
  a === b || (!!a && !!b && a.left === b.left && a.top === b.top && a.width === b.width && a.height === b.height);

/** What a panel's own window may do with "Customisable panels" on: move to the grid by its menu or a drag. */
function useWindowDock(): (panel: RegisteredPanel) => WindowDock | null {
  const customisable = useCustomisablePanels();
  const narrow = useIsNarrow();
  const doc = useClassicDoc();
  return (panel) =>
    customisable && !narrow && canDock(panel)
      ? {
          panelId: panel.id,
          actions: [moveAction(panel, doc)],
          onDragMove: (p) => moveDockDrag(panel.id, p.x, p.y),
          onDragEnd: (p) => dropPanel(panel.id, endDockDragAt(p.x, p.y)),
        }
      : null;
}

/** A mode's panel drawn over the 3D view: its content, edge to edge, above the canvas. */
function ViewportCover({panelId}: {panelId: PanelId}) {
  const panel = getPanel(panelId);
  if (!panel) return null;
  const props: PanelProps = {panelId, host: {kind: "overlay", zone: CLASSIC_ZONES.overlay}, close: () => {}, isActive: true};
  return (
    <div
      className="absolute inset-0 z-[5] overflow-auto bg-[var(--ada-window-bg,#111827)] text-[var(--ada-panel-text)]"
      data-viewport-cover={panelId}
      {...panelBodyAttrsFor(panelId)}
    >
      <ErrorBoundary label={panel.title}>
        <Suspense fallback={null}>
          <PanelMount panel={panel} provided={CLASSIC_PANEL_CONTENTS[panelId]} props={props} which="content" outerSuspense />
        </Suspense>
      </ErrorBoundary>
    </div>
  );
}

/** The classic frame: tree, top bar and column, viewport and windows, its zones filled from the registry. */
function ClassicFrame() {
  const {preset, env} = useLayout();
  const nodeEditorOnly = useNodeEditorStore((s) => s.use_node_editor_only);
  const mount = useMountRule();
  const customisable = useCustomisablePanels();
  const narrow = useIsNarrow();
  const doc = useClassicDoc();
  const windowDock = useWindowDock();
  // The in-viewer windows follow the preset's exclusive-group rule.
  React.useEffect(() => useViewerPanelStore.getState().setExclusive(!customisable), [customisable]);
  // A composed shell's starting panels; the host's controller is installed by now.
  // On load and on entering a mode of a composed shell: its plugin panels placed, its starting panels opened.
  const ctx = useClassicContext();
  const shellId = classicShellId();
  const lastCtx = React.useRef(ctx);
  const lastShell = React.useRef(shellId);
  // The prefs are loaded by now (the root waits for them): saved arrangements of an older shell version first,
  // and again for the shell an in-place switch brings in.
  React.useEffect(() => upgradeShellDocs(), [shellId]);
  React.useEffect(() => {
    // Leaving a mode takes its placed panels with it. Not leaving a whole
    // shell (an in-place switch): what is open stays open, at its place in
    // the shell switched to.
    if (lastCtx.current !== ctx && lastShell.current === shellId) leaveContext(lastCtx.current, hide, isPanelOpen);
    lastCtx.current = ctx;
    lastShell.current = shellId;
    adoptDockedPanels();
    openStartingPanels(reveal);
  }, [ctx, shellId]);
  // Plugin panels in a group or window of their own leave their region hosts.
  React.useEffect(() => setPlacedElsewhere(placedElsewhere(doc, customisable, narrow)), [doc, customisable, narrow]);
  React.useEffect(() => () => setPlacedElsewhere([]), []);
  // The main window of a multi-window dashboard: panels other windows send or give back, and those they show hidden here.
  React.useEffect(() => {
    const bus = windowBus();
    const uninstall = installMainWindow(bus, {reveal, hide, isOpen: isPanelOpen});
    // What mirrored panels in those windows read, and what they ask this one to do.
    registerCoreMirror();
    const unhost = hostMirror(bus);
    return () => {
      unhost();
      uninstall();
    };
  }, []);

  // The split grid: the docked panels that are open, around the viewport.
  const frameRef = React.useRef<HTMLDivElement>(null);
  const viewportRef = React.useRef<HTMLDivElement>(null);
  const gridOn = customisable && !narrow && !nodeEditorOnly;
  const docked = gridOn ? gridPanels(gridOf(doc)).map((id) => getPanel(id)).filter((p): p is RegisteredPanel => !!p && canDock(p)) : [];
  const states = usePanelStates(docked, env);
  const proceduralActive = useProceduralActive();
  const shows = (id: PanelId) => {
    const p = docked.find((x) => x.id === id);
    const s = p && states.get(id);
    return !!p && !!s && s.open && s.available && offeredInMode(p) && mounts(id, {state: s, env, proceduralActive});
  };
  const grid = gridOn ? shownGrid(gridOf(doc), shows) : null;
  const split = !!grid && grid.kind !== "viewport";
  const [vp, setVp] = React.useState<Rect | null>(null);
  const onViewportRect = React.useCallback((r: Rect) => setVp((v) => (sameRect(v, r) ? v : r)), []);
  const placed = split ? vp : null;
  const cover = classicCover();
  // HUDs over the view: the plugin panels of the `overlay` dock, while open (a buttonless one always is).
  const overlays = classicOverlays();
  const overlayPanels = listPanels({}).filter((p) => isOverlayPanel(p) && offeredInMode(p));
  const overlayStates = usePanelStates(overlayPanels, env);
  // The tree drawer at home overlays the frame's left edge (a push track for the
  // top bar): the grid starts right of it, as the top bar does.
  const isTreeCollapsed = useTreeViewStore((s) => s.isTreeCollapsed);
  const treeViewWidth = useTreeViewStore((s) => s.treeViewWidth);
  const wide = useIsWide();
  const treeInset = !isTreeCollapsed && wide && shownPlace(doc, CORE_PANEL_IDS.tree, customisable, !wide) === "home" ? treeViewWidth : 0;
  const where = (p: RegisteredPanel) => shownPlace(doc, p.id, customisable, narrow);
  // Home in the column or the tree track, for the layout's windows.
  // Plugin panels float through the same layer when they can.
  const floatable = [
    ...[...zonePanels(preset, CLASSIC_ZONES.menuColumn), ...zonePanels(preset, CLASSIC_ZONES.tree)].filter((p) => p.owner === CORE_PANEL_OWNER),
    ...listPanels().filter((p) => p.owner !== CORE_PANEL_OWNER && canFloat(p)),
  ].filter(offeredInMode);
  return (
    <div ref={frameRef} className={"relative flex flex-row h-full w-full bg-gray-900"}>
      {/* Tree View Section */}
      <div className={"relative h-full"}>
        <TreeZone />
      </div>
      {/* The Scene's right-click menu, for tree rows and 3D picks alike; portals to the body. */}
      <SceneMenuHost />

      <div className={"relative top-0 left-0"}>
        <ClassicMenu viewport={placed} />
      </div>

      {split && (
        <GridView grid={grid!} doc={doc} contents={CLASSIC_PANEL_CONTENTS} frame={frameRef} onViewportRect={onViewportRect} leftInset={treeInset} />
      )}

      {/* The viewport: rendered once, never inside a movable container. With the
          grid split, it is placed over the grid's viewport slot, never moved. */}
      <div
        ref={viewportRef}
        className={"w-full h-full"}
        {...(customisable ? {[ZONE_ATTR]: "viewport", tabIndex: -1} : {})}
        style={
          placed
            ? {position: "absolute", left: placed.left, top: placed.top, width: placed.width, height: placed.height}
            : cover || overlays
              ? {position: "relative"}
              : undefined
        }
      >
        {nodeEditorOnly ? <NodeEditorComponent /> : <CanvasWrapper legend={!overlays?.legend} />}
        {/* A composed shell's HUDs over the view, at their anchors. */}
        {overlays && !nodeEditorOnly && (
          <ViewportOverlays placement={overlays} pluginPanels={overlayPanels.filter((p) => overlayStates.get(p.id)?.open)} />
        )}
        {/* A mode's page over the view; the canvas stays mounted under it. */}
        {cover && <ViewportCover panelId={cover} />}
      </div>

      {/* Windows: the node editor in the tree, the attribute search in its own portal. */}
      <FloatLayer
        zone={CLASSIC_ZONES.float}
        mount={mount}
        provided={CLASSIC_PANEL_COMPONENTS}
        skip={(p) => where(p) === "grid"}
        windowDock={windowDock}
      />

      {/* Panels the user floated ("Customisable panels"), the drop targets, and the keys between zones. */}
      {customisable && <ClassicZoneKeys />}
      {/* Shells' and plugins' own shortcuts, and their panels'. */}
      <ShortcutKeys />

      {customisable && <ColumnDropTarget />}
      {gridOn && <ViewportDropTarget viewport={viewportRef} />}
      {customisable && <UndockedLayer panels={floatable} mounts={mount} contents={CLASSIC_PANEL_CONTENTS} />}

      {env.restMode === true && (
        <Suspense fallback={null}>
          <RestModeUI toastAnchor={classicToastAnchor() ?? undefined} />
        </Suspense>
      )}

      {/* The in-viewer admin / convert window, above the canvas and the tree but
          under the toast slot. */}
      <FloatLayer
        zone={CLASSIC_ZONES.window}
        mount={mount}
        chromeFor={() => ViewerWindowChrome}
        provided={CLASSIC_PANEL_COMPONENTS}
        skip={(p) => where(p) === "grid"}
        windowDock={windowDock}
      />
    </div>
  );
}

export const classicPreset: LayoutPreset = {
  id: CLASSIC_PRESET_ID,
  label: "Classic",
  description: "The viewer's own layout: a panel column under the top bar, the tree on the left.",
  owner: CORE_PANEL_OWNER,
  zones: [...CLASSIC_ZONE_DEFS],
  defaults: classicDefaults,
  // One in-viewer window at a time, as always, unless the user customises panels.
  exclusiveGroups: (prefs) => !customisablePanels(prefs),
  frame: () => Promise.resolve({default: ClassicFrame}),
};

/** The classic UI through the layout host. */
export default function ClassicLayout() {
  registerCorePanels();
  const env = React.useMemo(() => corePanelEnv(), []);
  const context = useClassicContext();
  React.useEffect(() => {
    if (!layoutStore.getState().hydrated) void layoutStore.getState().hydrate(viewerLayoutPersistence());
  }, []);
  return <LayoutHost preset={classicPreset} env={env} context={context} />;
}
