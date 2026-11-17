// The selected cell/equipment detail shown in the Selected Object Info panel:
// gizmo toggles, the geometry/parameter editors mirrored from the ada.topology
// pydantic entities, and the connected-systems list, each collapsible. Shows
// only while a procedural model has a selection. Model-wide actions (undo/redo,
// commit/compile, visibility toggles) live solely in the dedicated cellbuilder
// tool panel (CellBuilderPanel), not here.
//
// The pieces live in ./cellBuilderSelection/.

import React from "react";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { SelectionSection } from "./cellBuilderSelection/SelectionSection";

// Host: renders the selection detail inside the Selected Object Info panel when
// a procedural model has an active selection; otherwise nothing.
const CellBuilderSelectionInfo: React.FC = () => {
  const active = useCellBuilderStore((s) => s.active);
  const selection = useCellBuilderStore((s) => s.selection);
  if (!active || !selection) return null;
  return (
    <div className="mt-3 border-t border-gray-500/60 pt-2 text-xs text-white">
      <div className="font-bold mb-1">Procedural cell</div>
      <SelectionSection selection={selection} />
    </div>
  );
};

export default CellBuilderSelectionInfo;
