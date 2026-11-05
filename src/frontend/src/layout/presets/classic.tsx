// The classic preset: today's UI, as a layout of registered panels.
//
// Rendered through `LayoutHost` when the layout-engine flag is on (`../flag`),
// it must produce exactly the DOM of the hand-written tree it stands in for --
// `app.tsx`'s `AppBody` and `Menu.tsx` -- and a screenshot and DOM comparison
// holds it to that. So the frame below emits the same wrapper elements with the
// same classes, and the zones are filled from the registry:
//
//   classic.tree         the selection tree, always mounted (it hides itself)
//   (toolbar)            one toggle per core toolbar panel, in `toolbar.order`,
//                        drawn from `CLASSIC_TOOLBAR`, plus the plugin buttons
//                        at their slot -- the same `hidden!` / omitted rules as
//                        Menu.tsx
//   classic.menu-column  the stacked panels, in placement order, each its own
//                        card (`frame: "own"`), with the plugin `top-panel`
//                        region at its slot
//   classic.float        the node editor and the attribute search
//   classic.window       the in-viewer admin / convert window
//
// Visibility is each panel's binding -- the very flags Menu.tsx reads -- and the
// mount rules below restate Menu.tsx's render conditions where they differ from
// "open". Panel components come synchronously from the classic shell's own
// module graph (`../classicBodies`), and lazily, with the same Suspense
// boundaries, where Menu.tsx loads them lazily.

import React, {Suspense} from "react";

import CanvasWrapper from "@/components/viewer/CanvasWrapper";
import {SceneMenuHost} from "@/components/tree_view/SceneTreeMenu";
import NodeEditorComponent from "@/components/node_editor/NodeEditorComponent";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import CellBuilderContextMenu from "@/components/viewer/CellBuilderContextMenu";
import CellBuilderPortMenu from "@/components/viewer/CellBuilderPortMenu";
import CellBuilderInsertMenu from "@/components/viewer/CellBuilderInsertMenu";
import CellBuilderGizmoHud from "@/components/viewer/CellBuilderGizmoHud";
import {WebsocketStatusMenu} from "@/components/WebsocketStatusMenu";
import {ViewerWindow, VIEWER_PANEL_TITLE, viewerPanelExternal} from "@/components/InViewerPanelHost";
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

import {CLASSIC_PANEL_COMPONENTS, CLASSIC_PANEL_CONTENTS} from "../classicBodies";
import {CORE_PANEL_IDS, CORE_PANEL_OWNER, corePanelEnv, registerCorePanels} from "../corePanels";
import {FloatLayer, type WindowChrome} from "../FloatLayer";
import {CLASSIC_PRESET_ID, CLASSIC_ZONES, type LayoutPreset} from "../layout";
import {LayoutHost, PanelMount, usePanelStates, useLayout, zonePanels} from "../LayoutHost";
import {layoutStore} from "../layoutStore";
import {localLayoutPersistence} from "../localPersistence";
import type {PanelAction, PanelEnv, PanelId, PanelProps, RegisteredPanel} from "../panel";
import type {PanelState} from "../panelStates";
import {PLUGIN_MENU_COLUMN_ORDER, PLUGIN_TOOLBAR_ORDER} from "../pluginPanels";
import {listToolbarPanels} from "../registry";
import {hide, toggle} from "../reveal";
import {CLASSIC_TOOLBAR, CLASSIC_ZONE_DEFS, classicDefaults, classicMounts, toolbarPresence} from "./classicRules";
import {MinimisedTray, UndockedLayer, columnActions, isUndocked, useClassicDoc, useCustomisablePanels} from "./classicUndock";

const RestModeUI = React.lazy(() => import("@/components/rest_mode/RestModeUI"));

export function useProceduralActive(): boolean {
  return useCellBuilderStore((s) => s.active !== null || hasEmbeddedDoc(s));
}

const mounts = classicMounts;

// ---------------------------------------------------------------------------
// The top bar.
// ---------------------------------------------------------------------------

export function ToolbarButton({panel, state, env}: {panel: RegisteredPanel; state: PanelState; env: PanelEnv}) {
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
  const button = (p: RegisteredPanel) => <ToolbarButton key={p.id} panel={p} state={states.get(p.id)!} env={env} />;
  return (
    <div className={"flex flex-row items-center gap-2 px-2 max-w-full overflow-x-auto pointer-events-auto"}>
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

/** Error boundaries around column panels, as Menu.tsx puts them. */
const COLUMN_WRAP: Readonly<Record<PanelId, React.ComponentType<{children: React.ReactNode}>>> = {
  [CORE_PANEL_IDS.objectInfo]: ObjectInfoBoundary,
  [CORE_PANEL_IDS.scene]: SceneBoundary,
};

/** The cellbuilder's canvas menus and gizmo HUD, mounted beside its panel while a model is loaded. */
export const CellBuilderOverlays: React.FC = () => (
  <>
    <CellBuilderContextMenu />
    <CellBuilderPortMenu />
    <CellBuilderInsertMenu />
    <CellBuilderGizmoHud />
  </>
);

const NO_ACTIONS: readonly PanelAction[] = [];

function ColumnPanel({
  panel,
  state,
  env,
  proceduralActive,
  customisable,
}: {
  panel: RegisteredPanel;
  state: PanelState;
  env: PanelEnv;
  proceduralActive: boolean;
  customisable: boolean;
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
  return <PanelHeaderActionsProvider actions={customisable ? columnActions(panel) : NO_ACTIONS}>{wrapped}</PanelHeaderActionsProvider>;
}

function ClassicColumn({proceduralActive}: {proceduralActive: boolean}) {
  const {preset, env} = useLayout();
  const customisable = useCustomisablePanels();
  const doc = useClassicDoc();
  // Core's panels; the plugin `top-panel` region host draws the plugins' at their slot.
  const panels = zonePanels(preset, CLASSIC_ZONES.menuColumn).filter((p) => p.owner === CORE_PANEL_OWNER);
  const states = usePanelStates(panels, env);
  const render = (p: RegisteredPanel) => (
    <React.Fragment key={p.id}>
      {!(customisable && isUndocked(doc, p.id)) && (
        <ColumnPanel
          panel={p}
          state={states.get(p.id)!}
          env={env}
          proceduralActive={proceduralActive}
          customisable={customisable}
        />
      )}
      {p.id === CORE_PANEL_IDS.cellbuilder && proceduralActive && <CellBuilderOverlays />}
    </React.Fragment>
  );
  const order = (p: RegisteredPanel) => p.placement?.[CLASSIC_PRESET_ID]?.order ?? 0;
  return (
    <div className={"px-2 gap-2 flex flex-col pointer-events-auto max-w-[100vw]"}>
      {panels.filter((p) => order(p) < PLUGIN_MENU_COLUMN_ORDER).map(render)}
      <PluginPanelRegion region="top-panel" />
      {panels.filter((p) => order(p) >= PLUGIN_MENU_COLUMN_ORDER).map(render)}
    </div>
  );
}

/** The top bar over the panel column, pushed right by the open tree on wide screens. */
function ClassicMenu() {
  const nodeEditorOnly = useNodeEditorStore((s) => s.use_node_editor_only);
  const isTreeCollapsed = useTreeViewStore((s) => s.isTreeCollapsed);
  const treeViewWidth = useTreeViewStore((s) => s.treeViewWidth);
  const isWide = useIsWide();
  const proceduralActive = useProceduralActive();
  // The tree is a push track on desktop; on phones it overlays, as there is no
  // horizontal room to give up.
  const menuShiftPx = !isTreeCollapsed && isWide ? treeViewWidth : 0;
  return (
    <div className="relative w-full h-full">
      <div
        className="absolute left-0 top-0 z-10 py-2 gap-2 flex flex-col pointer-events-none transition-[padding] duration-150"
        style={{paddingLeft: `${menuShiftPx}px`}}
      >
        <ClassicToolbar nodeEditorOnly={!!nodeEditorOnly} />
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
  const panels = zonePanels(preset, CLASSIC_ZONES.tree);
  const states = usePanelStates(panels, env);
  return (
    <>
      {panels.map((p) =>
        mounts(p.id, {state: states.get(p.id)!, env, proceduralActive: false}) ? (
          <PanelMount
            key={p.id}
            panel={p}
            provided={CLASSIC_PANEL_COMPONENTS[p.id]}
            props={{panelId: p.id, host: {kind: "dock", zone: CLASSIC_ZONES.tree}, close: () => void hide(p.id), isActive: true}}
          />
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
  // A second window opens stepped down from the first, not on top of it.
  const index = which ? useViewerPanelStore.getState().shown.indexOf(which) : 0;
  return (
    <ViewerWindow
      offset={Math.max(0, index) * 32}
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

/** The classic frame: `AppBody`'s tree, its zones filled from the registry. */
function ClassicFrame() {
  const {preset, env} = useLayout();
  const nodeEditorOnly = useNodeEditorStore((s) => s.use_node_editor_only);
  const mount = useMountRule();
  const customisable = useCustomisablePanels();
  // The in-viewer windows follow the preset's exclusive-group rule.
  React.useEffect(() => useViewerPanelStore.getState().setExclusive(!customisable), [customisable]);
  return (
    <div className={"relative flex flex-row h-full w-full bg-gray-900"}>
      {/* Tree View Section */}
      <div className={"relative h-full"}>
        <TreeZone />
      </div>
      {/* The Scene's right-click menu, for tree rows and 3D picks alike; portals to the body. */}
      <SceneMenuHost />

      <div className={"relative top-0 left-0"}>
        <ClassicMenu />
      </div>

      {/* The viewport: rendered once, never inside a movable container. */}
      <div className={"w-full h-full"}>{nodeEditorOnly ? <NodeEditorComponent /> : <CanvasWrapper />}</div>

      {/* Windows: the node editor in the tree, the attribute search in its own portal. */}
      <FloatLayer zone={CLASSIC_ZONES.float} mount={mount} provided={CLASSIC_PANEL_COMPONENTS} />

      {/* Column panels the user floated ("Customisable panels"). */}
      {customisable && (
        <UndockedLayer
          panels={zonePanels(preset, CLASSIC_ZONES.menuColumn).filter((p) => p.owner === CORE_PANEL_OWNER)}
          mounts={mount}
          contents={CLASSIC_PANEL_CONTENTS}
        />
      )}

      {env.restMode === true && (
        <Suspense fallback={null}>
          <RestModeUI />
        </Suspense>
      )}

      {/* The in-viewer admin / convert window, above the canvas and the tree but
          under the toast slot. */}
      <FloatLayer
        zone={CLASSIC_ZONES.window}
        mount={mount}
        chromeFor={() => ViewerWindowChrome}
        provided={CLASSIC_PANEL_COMPONENTS}
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
  exclusiveGroups: (prefs) => prefs.customisablePanels !== true,
  frame: () => Promise.resolve({default: ClassicFrame}),
};

/** The classic UI through the layout host. */
export default function ClassicLayout() {
  registerCorePanels();
  const env = React.useMemo(() => corePanelEnv(), []);
  React.useEffect(() => {
    if (!layoutStore.getState().hydrated) void layoutStore.getState().hydrate(localLayoutPersistence());
  }, []);
  return <LayoutHost preset={classicPreset} env={env} />;
}
