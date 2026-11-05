import React from "react";

import DetailingPanel from "@/components/viewer/DetailingPanel";
import {PanelCard} from "@/components/ui/PanelFrame";
import {SheetHandle} from "@/components/ui/SheetHandle";
import {hasEmbeddedDoc, useCellBuilderStore} from "@/state/cellBuilderStore";
import {useEquipmentCatalogStore} from "@/state/equipmentCatalogStore";
import {useBottomSheet} from "@/utils/useBottomSheet";
import {offeredTabs, shownTab, type PanelTab} from "./cellbuilder/panel/chrome";
import {useLocalModelBrowser} from "./cellbuilder/panel/useLocalModelBrowser";
import {PanelHeader} from "./cellbuilder/panel/PanelHeader";
import {PanelTabBar} from "./cellbuilder/panel/PanelTabBar";
import {BuildTab} from "./cellbuilder/panel/BuildTab";
import {EquipmentTab} from "./cellbuilder/panel/EquipmentTab";
import {SystemsCatalogTab} from "./cellbuilder/panel/SystemsCatalogTab";
import {ViewTab} from "./cellbuilder/panel/ViewTab";
import {ToolsTab} from "./cellbuilder/panel/ToolsTab";
import {PanelFooter} from "./cellbuilder/panel/PanelFooter";

// The procedural-modelling context panel: add cells / typed equipment, list the
// boxes, edit systems, commit to postgres (revision-tracked) and compile via
// the worker. The per-selection cell/equipment detail lives in the Selected
// Object Info panel (see CellBuilderSelectionInfo), not here.
//
// Layout: a pinned header (model identity + undo/redo/close), a four-tab body
// (Build · Systems · View · Tools) whose long groups collapse, and a pinned
// footer (Commit + a Compile split-button). ⇧↵ commits and compiles in one
// gesture (see setupCameraControlsHandlers). On desktop the panel floats in the
// menu column; on a phone it docks as a bottom sheet. Toggled from its own
// top-row button in Menu (only rendered while a procedural model is loaded).
//
// The sections live in ./cellbuilder/panel — `CellBuilderBody` is the
// composition: which section renders where, and the little local UI state
// (which tab, which popover is open) they share. `CellBuilderPanel` is its
// classic frame (the card, the phone's bottom sheet).

/**
 * The panel's local UI state: which tab, which popover is open, the local-disk
 * model browser. A hook of its own so the classic frame can keep it while the
 * panel is hidden -- toggling the panel off and on returns to the same tab, and a
 * focused system still switches to the Systems tab while the panel is closed.
 */
export function useCellBuilderPanelState() {
  const equipBtnRef = React.useRef<HTMLButtonElement>(null);
  const openingBtnRef = React.useRef<HTMLButtonElement>(null);
  const [equipMenuOpen, setEquipMenuOpen] = React.useState(false);
  const [openingMenuOpen, setOpeningMenuOpen] = React.useState(false);
  const [tab, setTab] = React.useState<PanelTab>("build");
  const browser = useLocalModelBrowser();

  // Clicking a routed run focuses its system — surface it by switching to the
  // Systems tab (SystemsTab then scrolls it into view).
  const focusedSystem = useCellBuilderStore((s) => s.focusedSystemName);
  React.useEffect(() => {
    if (focusedSystem) setTab("systems");
  }, [focusedSystem]);

  // Load the per-scope catalog for the tab being opened (mirrors what the old
  // "Equipment/System overview" toggle buttons did on open).
  React.useEffect(() => {
    if (tab === "equipment")
      void useEquipmentCatalogStore.getState().refreshEquipment();
    else if (tab === "systems")
      void useEquipmentCatalogStore.getState().refreshSystems();
  }, [tab]);

  // The Detailing tab only exists while a detailing engine is selected — if it is
  // turned back to "none" while that tab is open, fall back to Build so the body
  // isn't left showing nothing.
  const detailingSelected = useCellBuilderStore((s) => s.selectedDetailing !== "none");
  React.useEffect(() => {
    if (!detailingSelected)
      setTab((t) => (t === "detailing" ? "build" : t));
  }, [detailingSelected]);

  return {
    equipBtnRef, openingBtnRef,
    equipMenuOpen, setEquipMenuOpen,
    openingMenuOpen, setOpeningMenuOpen,
    tab, setTab,
    browser,
  };
}

export type CellBuilderPanelState = ReturnType<typeof useCellBuilderPanelState>;

export type CellBuilderTab = PanelTab;

export interface CellBuilderBodyProps {
  /** The tabs offered, in order. Default: all; Detailing only while an engine is selected. */
  tabs?: readonly CellBuilderTab[];
  tabLabels?: Partial<Record<CellBuilderTab, string>>;
  /** Undo and redo in the header. Default on. */
  showUndoRedo?: boolean;
  /** The Build tab's add buttons (+ Cell, + Opening, + Loft, + Equipment). Default on. */
  showAddButtons?: boolean;
  /** The Build tab's "Cells & equipment" list. Default on. */
  showComponentList?: boolean;
  /** Shown instead of the panel while no model is open. Default: the panel, empty. */
  emptyState?: React.ReactNode;
}

/** The cellbuilder panel's content, with its own UI state. */
export const CellBuilderBody: React.FC<CellBuilderBodyProps> = (props) => (
  <CellBuilderContent state={useCellBuilderPanelState()} {...props} />
);

/** The content, from its pinned header to its pinned footer, over state its host keeps. */
export const CellBuilderContent: React.FC<{state: CellBuilderPanelState} & CellBuilderBodyProps> = ({
  state,
  tabs,
  tabLabels,
  showUndoRedo = true,
  showAddButtons = true,
  showComponentList = true,
  emptyState,
}) => {
  const s = useCellBuilderStore();
  const {
    equipBtnRef, openingBtnRef,
    equipMenuOpen, setEquipMenuOpen,
    openingMenuOpen, setOpeningMenuOpen,
    tab, setTab,
    browser,
  } = state;
  const compileState = s.compileJob;
  const offered = offeredTabs(tabs, s.selectedDetailing !== "none");
  const shown = shownTab(tab, offered);

  if (emptyState !== undefined && s.active === null && !hasEmbeddedDoc(s)) return <>{emptyState}</>;

  return (
    <>
      <PanelHeader browser={browser} showUndoRedo={showUndoRedo} />
      <PanelTabBar tab={shown} setTab={setTab} tabs={offered} labels={tabLabels} />

      {/* ── scrollable body ── */}
            <div className="flex-1 overflow-y-auto p-2.5 min-h-0">
                <div className={shown === "build" ? "flex flex-col gap-2" : "hidden"}>
          <BuildTab
            equipBtnRef={equipBtnRef}
            openingBtnRef={openingBtnRef}
            equipMenuOpen={equipMenuOpen}
            setEquipMenuOpen={setEquipMenuOpen}
            openingMenuOpen={openingMenuOpen}
            setOpeningMenuOpen={setOpeningMenuOpen}
            showAddButtons={showAddButtons}
            showComponentList={showComponentList}
          />
        </div>

                <div className={shown === "equipment" ? "block" : "hidden"}>
                    <EquipmentTab active={shown === "equipment"} />
        </div>

                <div className={shown === "systems" ? "block" : "hidden"}>
                    <SystemsCatalogTab active={shown === "systems"} />
        </div>

                {/* DETAILING — data-driven from the selected engine's joint_types */}
                <div className={shown === "detailing" ? "block" : "hidden"}>
          <DetailingPanel />
        </div>

                <div className={shown === "view" ? "flex flex-col gap-2" : "hidden"}>
          <ViewTab />
        </div>

                <div className={shown === "tools" ? "flex flex-col gap-2" : "hidden"}>
          <ToolsTab />
        </div>
      </div>

      {/* ── error / conflict banners (visible on any tab) ── */}
      {(s.conflict || compileState?.status === "error") && (
        <div className="px-2.5 py-1 border-t border-gray-600/50">
          {s.conflict && <p className="text-red-400">{s.conflict}</p>}
          {compileState?.status === "error" && (
            <p className="text-red-400">
              Compile failed: {compileState.error}
            </p>
          )}
        </div>
      )}

      <PanelFooter onSaved={browser.refresh} />
    </>
  );
};

const CellBuilderPanel: React.FC = () => {
  const loaded = useCellBuilderStore((s) => s.active !== null || hasEmbeddedDoc(s));
  const panelVisible = useCellBuilderStore((s) => s.panelVisible);
  const sheet = useBottomSheet(() => useCellBuilderStore.getState().setPanelVisible(false));
  const state = useCellBuilderPanelState();

  // Either an editable session (`active`) or a view-only document loaded off a
  // GLB (`hasEmbeddedDoc`) is enough to show the panel; what differs is what the
  // panel LETS YOU DO, which each section decides per-control with `isReadOnly`
  // rather than by hiding the whole tool.
  if (!loaded || !panelVisible) return null;

  return (
    <PanelCard
      ref={sheet.panelRef}
      style={
        sheet.isMobile && sheet.sheetPx != null
          ? {height: `${sheet.sheetPx}px`, maxHeight: `${sheet.sheetPx}px`}
          : undefined
      }
      variant="surface"
      className={
        "text-xs pointer-events-auto flex flex-col " +
        // mobile: dock as a bottom sheet; desktop: float in the menu column.
        "fixed inset-x-0 bottom-0 z-30 w-full max-h-[82vh] rounded-t-2xl " +
        "sm:static sm:z-auto sm:w-[340px] sm:max-w-[380px] " +
        "sm:max-h-[calc(100vh-7rem)] sm:rounded-md"
      }
    >
      {/* mobile grab handle — drag to resize the sheet, flick down to dismiss */}
      <SheetHandle grab={sheet.grab} className="sm:hidden" />
      <CellBuilderContent state={state} />
    </PanelCard>
  );
};

export default CellBuilderPanel;
