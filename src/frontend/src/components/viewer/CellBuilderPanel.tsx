import React from "react";

import DetailingPanel from "@/components/viewer/DetailingPanel";
import {hasEmbeddedDoc, useCellBuilderStore} from "@/state/cellBuilderStore";
import {useEquipmentCatalogStore} from "@/state/equipmentCatalogStore";
import {CHROME, type PanelTab} from "./cellbuilder/panel/chrome";
import {useLocalModelBrowser} from "./cellbuilder/panel/useLocalModelBrowser";
import {useSheetDrag} from "./cellbuilder/panel/useSheetDrag";
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
// The sections live in ./cellbuilder/panel — this file is the composition:
// which section renders where, and the little local UI state (which tab, which
// popover is open) they share.

const CellBuilderPanel: React.FC = () => {
  const s = useCellBuilderStore();
  const equipBtnRef = React.useRef<HTMLButtonElement>(null);
  const openingBtnRef = React.useRef<HTMLButtonElement>(null);
  const [equipMenuOpen, setEquipMenuOpen] = React.useState(false);
  const [openingMenuOpen, setOpeningMenuOpen] = React.useState(false);
  const [tab, setTab] = React.useState<PanelTab>("build");
  const browser = useLocalModelBrowser();
  const sheet = useSheetDrag(() => s.setPanelVisible(false));

  // Clicking a routed run focuses its system — surface it by switching to the
  // Systems tab (SystemsTab then scrolls it into view).
  const focusedSystem = s.focusedSystemName;
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
  const detailingSelected = s.selectedDetailing !== "none";
  React.useEffect(() => {
    if (!detailingSelected)
      setTab((t) => (t === "detailing" ? "build" : t));
  }, [detailingSelected]);

  // Either an editable session (`active`) or a view-only document loaded off a
  // GLB (`hasEmbeddedDoc`) is enough to show the panel; what differs is what the
  // panel LETS YOU DO, which each section decides per-control with `isReadOnly`
  // rather than by hiding the whole tool.
  if ((!s.active && !hasEmbeddedDoc(s)) || !s.panelVisible) return null;

  const compileState = s.compileJob;

  return (
    <div
      ref={sheet.panelRef}
      style={
        sheet.isMobile && sheet.sheetPx != null
          ? {height: `${sheet.sheetPx}px`, maxHeight: `${sheet.sheetPx}px`}
          : undefined
      }
      className={
        CHROME +
        " text-xs pointer-events-auto flex flex-col " +
        // mobile: dock as a bottom sheet; desktop: float in the menu column.
        "fixed inset-x-0 bottom-0 z-30 w-full max-h-[82vh] rounded-t-2xl " +
        "sm:static sm:z-auto sm:w-[340px] sm:max-w-[380px] " +
        "sm:max-h-[calc(100vh-7rem)] sm:rounded-md"
      }
    >
      {/* mobile grab handle — drag to resize the sheet, flick down to dismiss */}
      <div
        className="sm:hidden shrink-0 flex justify-center items-center py-2 cursor-grab active:cursor-grabbing touch-none"
        onPointerDown={sheet.onGrabDown}
        onPointerMove={sheet.onGrabMove}
        onPointerUp={sheet.onGrabUp}
        onPointerCancel={sheet.onGrabUp}
        role="separator"
        aria-label="Drag to resize the panel"
      >
        <span
          className="block w-10 h-1.5 rounded-full bg-gray-400/70"
          aria-hidden="true"
        />
      </div>

      <PanelHeader browser={browser} />
      <PanelTabBar tab={tab} setTab={setTab} />

      {/* ── scrollable body ── */}
            <div className="flex-1 overflow-y-auto p-2.5 min-h-0">
                <div className={tab === "build" ? "flex flex-col gap-2" : "hidden"}>
          <BuildTab
            equipBtnRef={equipBtnRef}
            openingBtnRef={openingBtnRef}
            equipMenuOpen={equipMenuOpen}
            setEquipMenuOpen={setEquipMenuOpen}
            openingMenuOpen={openingMenuOpen}
            setOpeningMenuOpen={setOpeningMenuOpen}
          />
        </div>

                <div className={tab === "equipment" ? "block" : "hidden"}>
                    <EquipmentTab active={tab === "equipment"} />
        </div>

                <div className={tab === "systems" ? "block" : "hidden"}>
                    <SystemsCatalogTab active={tab === "systems"} />
        </div>

                {/* DETAILING — data-driven from the selected engine's joint_types */}
                <div className={tab === "detailing" ? "block" : "hidden"}>
          <DetailingPanel />
        </div>

                <div className={tab === "view" ? "flex flex-col gap-2" : "hidden"}>
          <ViewTab />
        </div>

                <div className={tab === "tools" ? "flex flex-col gap-2" : "hidden"}>
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
    </div>
  );
};

export default CellBuilderPanel;
