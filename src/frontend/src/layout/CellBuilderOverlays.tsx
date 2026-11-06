// The procedural builder's canvas menus and gizmo HUD: mounted beside its panel in
// the classic UI, and by a composed shell that asks for them (`chrome.overlays.builder`).

import React from "react";

import CellBuilderContextMenu from "@/components/viewer/CellBuilderContextMenu";
import CellBuilderPortMenu from "@/components/viewer/CellBuilderPortMenu";
import CellBuilderInsertMenu from "@/components/viewer/CellBuilderInsertMenu";
import CellBuilderGizmoHud from "@/components/viewer/CellBuilderGizmoHud";

/** The cellbuilder's canvas menus and gizmo HUD, mounted while a procedural model is loaded. */
export const CellBuilderOverlays: React.FC = () => (
  <>
    <CellBuilderContextMenu />
    <CellBuilderPortMenu />
    <CellBuilderInsertMenu />
    <CellBuilderGizmoHud />
  </>
);
