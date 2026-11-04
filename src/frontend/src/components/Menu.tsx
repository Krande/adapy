import React, { Suspense } from "react";
import { useIsWide } from "@/utils/useIsMobile";
import ObjectInfoBox from "./info_box_selected_object/ObjectInfoBoxComponent";
import SimulationControls from "./simulation/SimulationControls";
import { feModelLoaded, hasFemConcepts, simulationButtonShown } from "./simulation/simPanelTabs";
import { useFemConceptsStore } from "@/state/femConceptsStore";
import ComponentControls from "./component_view/ComponentControls";
import { useComponentControlsStore } from "@/state/componentControlsStore";
import { useComponentSpecsStore } from "@/state/componentSpecsStore";
import { request_list_of_nodes } from "../utils/node_editor/handlers/request_list_of_nodes";
import ServerInfoBox from "./server_info/ServerInfoBox";
import { runtime } from "@/runtime/config";
import { useViewerStores } from "../state/AdaViewerContext";
// REST-only — code-split so the embedded desktop zip stays slim.
// Scope / user / admin controls now live inside the options drawer
// (RestSection); the menu bar is kept tight so it stays usable on
// phones.
const StorageBrowser = React.lazy(() => import("./storage/StorageBrowser"));
import OptionsComponent from "./OptionsComponent";
import GraphIcon from "./icons/GraphIcon";
import InfoIcon from "./icons/InfoIcon";
import ReloadIcon from "./icons/ReloadIcon";
import ServerIcon from "./icons/ServerIcon";
import AttributeSearchIcon from "./icons/AttributeSearchIcon";
import { SEARCH_WINDOW_TITLE, useSearchWindowStore } from "@/state/searchWindowStore";
import ToggleControlsIcon from "./icons/AnimationControlToggle";
import TreeViewIcon from "./icons/TreeViewIcon";
import SceneIcon from "./icons/SceneIcon";
import ComponentIcon from "./icons/ComponentIcon";
import CellBuilderIcon from "./icons/CellBuilderIcon";
import CellBuilderPanel from "./viewer/CellBuilderPanel";
import CellBuilderContextMenu from "./viewer/CellBuilderContextMenu";
import CellBuilderPortMenu from "./viewer/CellBuilderPortMenu";
import CellBuilderInsertMenu from "./viewer/CellBuilderInsertMenu";
import CellBuilderGizmoHud from "./viewer/CellBuilderGizmoHud";
import { hasEmbeddedDoc, useCellBuilderStore } from "@/state/cellBuilderStore";
import ErrorBoundary from "./common/ErrorBoundary";
import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";
// Equipment catalog editor — opened contextually from an equipment's "Edit
// properties" (no top-row button); code-split so the embedded zip stays slim.
const EquipmentAdminPanel = React.lazy(
  () => import("./admin/EquipmentAdminPanel"),
);
const SystemAdminPanel = React.lazy(() => import("./admin/SystemAdminPanel"));
import SceneInfoBox from "./info_box_scene/SceneInfoBox";
import { WebsocketStatusMenu, WebsocketStatusBox } from "./WebsocketStatusMenu";
import { PluginTopBarButtons, PluginPanelRegion } from "@/plugins";
import { navBtnClass } from "./navButton";


const Menu = () => {
  const stores = useViewerStores();
  const { show_info_box } = stores.useObjectInfoStore();
  const { show_scene_info_box } = stores.useSceneInfoStore();
  // Selection identity — drives the info-panel ErrorBoundary's auto-reset so a
  // panel that crashed on one selection retries on the next (rather than
  // staying stuck on the fallback until manually retried).
  const infoName = stores.useObjectInfoStore((s) => s.name);
  const cbSelectionKey = useCellBuilderStore(
    (s) => s.selection?.cellId ?? null,
  );
  const { isNodeEditorVisible, setIsNodeEditorVisible, use_node_editor_only } =
    stores.useNodeEditorStore();
  const { isOptionsVisible, setIsOptionsVisible, enableNodeEditor } =
    stores.useOptionsStore(); // use the useNavBarStore function
  const { showServerInfoBox, setShowServerInfoBox } =
    stores.useServerInfoStore();
  const { hasAnimation, isControlsVisible, setIsControlsVisible } =
    stores.useAnimationStore();
  const feaSessionActive = stores.useFeaAnimationStore((s) => s.sessionActive);
  // An FE model without results still has the panel's Model tab to show.
  const feaManifest = stores.useFeaAnimationStore((s) => s.manifest);
  const femHasConcepts = useFemConceptsStore(hasFemConcepts);
  const simulationButton = simulationButtonShown({
    hasAnimation,
    sessionActive: feaSessionActive,
    feModel: feModelLoaded({ manifest: feaManifest, femHasConcepts }),
  });
  const componentControlsVisible = useComponentControlsStore(
    (s) => s.isVisible,
  );
  const toggleComponentControls = useComponentControlsStore(
    (s) => s.toggleVisible,
  );
  // Button only renders when the current scope actually has baked
  // component specs. Auto-refetches on scope change via
  // subscribeSpecsToScope (mounted in AuthGate).
  const componentSpecsAvailable = useComponentSpecsStore((s) => s.hasSpecs);
  // Procedural-context button only renders while a procedural model is
  // loaded in the cellbuilder; closing the model hides button + panel.
  //
  // Either kind of "loaded" counts: an editable session (`active`, opened from
  // the model store) or a document that arrived embedded in the GLB
  // (`hasEmbeddedDoc`, the websocket/desktop path -- `assembly.show()`). The
  // second is read-only for now and the panel says so, but it is exactly as
  // worth browsing: it is the equipment and systems of the model on screen.
  const proceduralActive = useCellBuilderStore(
    (s) => s.active !== null || hasEmbeddedDoc(s),
  );
  const cellBuilderPanelVisible = useCellBuilderStore((s) => s.panelVisible);
  const toggleCellBuilderPanel = () =>
    useCellBuilderStore
      .getState()
      .setPanelVisible(!useCellBuilderStore.getState().panelVisible);
  const equipmentPanelOpen = useEquipmentCatalogStore(
    (s) => s.equipmentPanelOpen,
  );
  const systemPanelOpen = useEquipmentCatalogStore((s) => s.systemPanelOpen);
  const { showInfoBox: showWebsocketInfoBox } =
    stores.useWebsocketStatusStore();
  // Field by field, not the whole store: the menu bar would otherwise re-render
  // on every tree change, selection included.
  const isTreeCollapsed = stores.useTreeViewStore((s) => s.isTreeCollapsed);
  const setIsTreeCollapsed = stores.useTreeViewStore((s) => s.setIsTreeCollapsed);
  const treeViewWidth = stores.useTreeViewStore((s) => s.treeViewWidth);
  // The `md:` (wide) breakpoint, followed as the viewport changes (rotating a
  // tablet, dragging the window across it).
  const isDesktop = useIsWide();
  const searchWindowOpen = useSearchWindowStore((s) => s.open);

  // On desktop the tree panel pushes the menu bar to its right so
  // the buttons aren't hidden behind it. Mobile keeps overlay
  // behaviour — there's no horizontal room to give up, and the user
  // closes the tree to reach the menu anyway.
  const menuShiftPx = !isTreeCollapsed && isDesktop ? treeViewWidth : 0;

  return (
    <div className="relative w-full h-full">
      <div
        className="absolute left-0 top-0 z-10 py-2 gap-2 flex flex-col pointer-events-none transition-[padding] duration-150"
        style={{ paddingLeft: `${menuShiftPx}px` }}
      >
        <div
          className={
            "flex flex-row items-center gap-2 px-2 max-w-full overflow-x-auto pointer-events-auto"
          }
        >
          {use_node_editor_only && (
            <button
              className={
                "flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 rounded-sm transition-colors"
              }
              onClick={() => void request_list_of_nodes().catch((err) => console.error("Failed to reload nodes:", err))}
              title="Reload nodes"
            >
              <ReloadIcon />
            </button>
          )}

          <button
            className={navBtnClass(isOptionsVisible, "", use_node_editor_only)}
            hidden={use_node_editor_only}
            onClick={() => setIsOptionsVisible(!isOptionsVisible)}
            title="Toggle options drawer"
            aria-pressed={isOptionsVisible}
          >
            ☰
          </button>

          <button
            className={navBtnClass(!isTreeCollapsed, "", use_node_editor_only)}
            hidden={use_node_editor_only}
            onClick={() => setIsTreeCollapsed(!isTreeCollapsed)}
            title={
              isTreeCollapsed
                ? "Show selection tree (Shift+T)"
                : "Hide selection tree (Shift+T)"
            }
            aria-label={
              isTreeCollapsed ? "Show selection tree" : "Hide selection tree"
            }
            aria-pressed={!isTreeCollapsed}
          >
            <TreeViewIcon />
          </button>

          <button
            className={navBtnClass(
              isNodeEditorVisible,
              "",
              use_node_editor_only || !enableNodeEditor,
            )}
            hidden={use_node_editor_only || !enableNodeEditor}
            onClick={() => setIsNodeEditorVisible(!isNodeEditorVisible)}
            title="Toggle node editor"
            aria-pressed={isNodeEditorVisible}
          >
            <GraphIcon />
          </button>
          {runtime.isRestMode() && (
            <button
              className={navBtnClass(showServerInfoBox)}
              onClick={() => setShowServerInfoBox(!showServerInfoBox)}
              title="Storage"
              aria-pressed={showServerInfoBox}
            >
              <ServerIcon />
            </button>
          )}
          {/* The attribute search spans every collection of every provider in the scope -- a
              viewer-wide tool, so its entry point is here rather than in one collection's tree. */}
          {runtime.isRestMode() && (
            <button
              className={navBtnClass(searchWindowOpen, "", use_node_editor_only)}
              hidden={use_node_editor_only}
              onClick={useSearchWindowStore.getState().toggle}
              title={SEARCH_WINDOW_TITLE}
              aria-label={SEARCH_WINDOW_TITLE}
              aria-pressed={searchWindowOpen}
            >
              <AttributeSearchIcon />
            </button>
          )}
          <button
            className={navBtnClass(show_info_box, "", use_node_editor_only)}
            hidden={use_node_editor_only}
            onClick={stores.useObjectInfoStore.getState().toggle}
            title="Toggle object info"
            aria-pressed={show_info_box}
          >
            <InfoIcon />
          </button>
          <button
            className={navBtnClass(
              show_scene_info_box,
              "",
              use_node_editor_only,
            )}
            hidden={use_node_editor_only}
            onClick={stores.useSceneInfoStore.getState().toggle}
            title="Toggle scene info"
            aria-label="Toggle scene info"
            aria-pressed={show_scene_info_box}
          >
            <SceneIcon />
          </button>

          <button
            className={navBtnClass(
              isControlsVisible,
              "",
              !simulationButton,
            )}
            hidden={!simulationButton}
            onClick={() => setIsControlsVisible(!isControlsVisible)}
            title="Toggle simulation panel"
            aria-pressed={isControlsVisible}
          >
            <ToggleControlsIcon />
          </button>
          <button
            className={navBtnClass(
              componentControlsVisible,
              "",
              use_node_editor_only || !componentSpecsAvailable,
            )}
            hidden={use_node_editor_only || !componentSpecsAvailable}
            onClick={toggleComponentControls}
            title="Toggle connection-component panel"
            aria-label="Toggle connection-component panel"
            aria-pressed={componentControlsVisible}
          >
            <ComponentIcon />
          </button>
          <button
            className={navBtnClass(
              cellBuilderPanelVisible,
              "",
              use_node_editor_only || !proceduralActive,
            )}
            hidden={use_node_editor_only || !proceduralActive}
            onClick={toggleCellBuilderPanel}
            title="Toggle procedural cellbuilder panel"
            aria-label="Toggle procedural cellbuilder panel"
            aria-pressed={cellBuilderPanelVisible}
          >
            <CellBuilderIcon />
          </button>
          {/* Equipment / system catalogs live in the Admin panel
                        (Equipment / System tabs) — no separate top-row buttons. */}
          {/* Plugin-contributed top-bar buttons (region "top-panel"). Core
              iterates the registry; no plugin is named here. */}
          <PluginTopBarButtons navBtnClass={(active) => navBtnClass(active)} />
          {/* UI-shell switcher — renders nothing unless this build carries an
              alternative UI (a plugin-contributed shell). See plugins/uiShells. */}
          {/* No connection button in a static embed (window.DEACTIVATE_WS: the docs'
              3D figures, notebook outputs): there is no session to show or reconnect. */}
          {!runtime.isRestMode() && !runtime.websocketDeactivated() && (
            <div className={navBtnClass(showWebsocketInfoBox)}>
              <WebsocketStatusMenu />
            </div>
          )}
        </div>
        <div
          className={
            "px-2 gap-2 flex flex-col pointer-events-auto max-w-[100vw]"
          }
        >
          {isOptionsVisible && <OptionsComponent />}
          {showServerInfoBox &&
            (runtime.isRestMode() ? (
              <Suspense fallback={null}>
                <StorageBrowser />
              </Suspense>
            ) : (
              <ServerInfoBox />
            ))}
          {show_info_box && (
            <ErrorBoundary
              label="Selected object info"
              resetKeys={[infoName, cbSelectionKey]}
            >
              <ObjectInfoBox />
            </ErrorBoundary>
          )}
          {show_scene_info_box && (
            <ErrorBoundary label="Scene info">
              <SceneInfoBox />
            </ErrorBoundary>
          )}
          {showWebsocketInfoBox && !runtime.websocketDeactivated() && <WebsocketStatusBox />}
          {isControlsVisible && <SimulationControls />}
          {componentControlsVisible && <ComponentControls />}
          {proceduralActive && <CellBuilderPanel />}
          {proceduralActive && <CellBuilderContextMenu />}
          {proceduralActive && <CellBuilderPortMenu />}
          {proceduralActive && <CellBuilderInsertMenu />}
          {proceduralActive && <CellBuilderGizmoHud />}
          {proceduralActive && equipmentPanelOpen && (
            <Suspense fallback={null}>
              <EquipmentAdminPanel />
            </Suspense>
          )}
          {proceduralActive && systemPanelOpen && (
            <Suspense fallback={null}>
              <SystemAdminPanel />
            </Suspense>
          )}
          {/* Plugin-contributed panels (region "top-panel"). Each is wrapped in
              an ErrorBoundary inside PluginPanelRegion so a plugin crash is
              contained. Renders nothing when no plugin is active. */}
          <PluginPanelRegion region="top-panel" />
        </div>
      </div>
    </div>
  );
};

export default Menu;
