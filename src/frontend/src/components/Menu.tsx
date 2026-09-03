import React, { Suspense, useEffect, useState } from "react";
import ObjectInfoBox from "./info_box_selected_object/ObjectInfoBoxComponent";
import SimulationControls from "./simulation/SimulationControls";
import ComponentControls from "./component_view/ComponentControls";
import { useComponentControlsStore } from "@/state/componentControlsStore";
import { useComponentSpecsStore } from "@/state/componentSpecsStore";
import { request_list_of_nodes } from "../utils/node_editor/handlers/request_list_of_nodes";
import ServerInfoBox from "./server_info/ServerInfoBox";
import ExternalModelsPanel from "./ExternalModelsPanel";
import { useExternalModelsStore } from "@/state/externalModelsStore";
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
import ExternalModelsIcon from "./icons/ExternalModelsIcon";
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
import { useCellBuilderStore } from "@/state/cellBuilderStore";
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

// `md:` Tailwind breakpoint. Match it with matchMedia so the menu can
// react to viewport changes (rotating a tablet, dragging the window
// across breakpoints) without re-architecting around CSS-only rules.
const DESKTOP_QUERY = "(min-width: 768px)";

// Top-bar button styling. Inactive uses a translucent hover (lighter
// than the base) so the button "lifts" on hover. Active uses a darker
// blue + inset shadow so the button reads as "pressed" — distinct from
// hover (lighter) and from the base (mid-tone). Applied to every
// toggle in the bar so the whole row uses one visual vocabulary.
//
// Mobile (<md): fixed 40x40 inline-flex box so every entry takes the
// same horizontal slot — keeps the row inside a 360px viewport even
// at the busiest combo (options + tree + node-editor + storage + info
// + scene + animation + ws status). The 24px SVG icons centre via
// flex; the lone ☰ text glyph keeps its size with font-bold.
//
// Desktop (md+): drop the fixed box in favour of padding-based sizing
// (``py-2 px-4``). Icon buttons end up ~40 % wider than the narrow ☰
// glyph, which reads as a clearer visual rhythm when the row isn't
// space-constrained.
//
// The base class includes ``inline-flex``, which is what gives the
// uniform box on mobile but also outranks the UA stylesheet's
// ``[hidden] { display: none }`` rule in the cascade. ``navBtnClass``
// takes an explicit ``hidden`` boolean and folds in Tailwind's
// ``!hidden`` (display: none !important) when set — that way the
// HTML ``hidden`` attribute doesn't have to fight ``inline-flex``
// and the simulation-controls / node-editor toggles stay properly
// gated on their ``hasAnimation``/``feaSessionActive``/``enableNodeEditor``
// state.
//
// Hover styles are gated with ``pointer-fine:`` (mouse-only) instead
// of plain ``hover:``. ``hover:`` resolves to ``@media (hover: hover)``
// which Android Chrome and many hybrid tablets report TRUE on touch-
// primary devices because the hardware *could* accept a stylus or
// paired mouse — leaving :hover sticky on touch. After tapping a
// button to toggle the menu OFF, the now-inactive button rendered
// the translucent ``bg-blue-700/50`` hover style and looked
// half-pressed. ``pointer: coarse`` is the touch canonical, so
// ``pointer-fine`` is the safe mouse-only gate.
const NAV_BTN_BASE =
  "inline-flex items-center justify-center w-10 h-10 shrink-0 rounded-sm " +
  "md:w-auto md:h-auto md:py-2 md:px-4 md:rounded " +
  "text-white font-bold transition-colors";
const NAV_BTN_INACTIVE = "bg-blue-700 pointer-fine:hover:bg-blue-700/50";
const NAV_BTN_ACTIVE =
  "bg-blue-900 pointer-fine:hover:bg-blue-800 shadow-inner";

function navBtnClass(
  active: boolean,
  extra: string = "",
  hidden: boolean = false,
): string {
  const hiddenClass = hidden ? "hidden!" : "";
  return `${NAV_BTN_BASE} ${active ? NAV_BTN_ACTIVE : NAV_BTN_INACTIVE} ${hiddenClass} ${extra}`
    .replace(/\s+/g, " ")
    .trim();
}

function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(
    () =>
      typeof window !== "undefined" && window.matchMedia(DESKTOP_QUERY).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_QUERY);
    const onChange = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return isDesktop;
}

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
  const externalModelsVisible = useExternalModelsStore((s) => s.visible);
  const toggleExternalModels = useExternalModelsStore((s) => s.toggle);
  const { showServerInfoBox, setShowServerInfoBox } =
    stores.useServerInfoStore();
  const { hasAnimation, isControlsVisible, setIsControlsVisible } =
    stores.useAnimationStore();
  const feaSessionActive = stores.useFeaAnimationStore((s) => s.sessionActive);
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
  const proceduralActive = useCellBuilderStore((s) => s.active !== null);
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
  const { isTreeCollapsed, setIsTreeCollapsed, treeViewWidth } =
    stores.useTreeViewStore();
  const isDesktop = useIsDesktop();

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
              onClick={() => request_list_of_nodes()}
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
          {/* External models — REST-only for the same reason as Storage: the
              catalogue is served by the REST API. Hidden entirely rather than
              disabled when the scope has no binding would require reading the
              setting on every scope change just to decide whether to draw a
              button, so the button stays and the PANEL explains. */}
          {runtime.isRestMode() && (
            <button
              className={navBtnClass(externalModelsVisible)}
              onClick={toggleExternalModels}
              title="External models"
              aria-pressed={externalModelsVisible}
            >
              <ExternalModelsIcon />
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
              !hasAnimation && !feaSessionActive,
            )}
            hidden={!hasAnimation && !feaSessionActive}
            onClick={() => setIsControlsVisible(!isControlsVisible)}
            title="Toggle animation controls"
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
          {!runtime.isRestMode() && (
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
          {showWebsocketInfoBox && <WebsocketStatusBox />}
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
          <ExternalModelsPanel />
          <PluginPanelRegion region="top-panel" />
        </div>
      </div>
    </div>
  );
};

export default Menu;
