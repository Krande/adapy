/**
 * The cell builder's GRID & SNAPPING settings: the grid step, the snap distance,
 * what a click selects, vertex snapping, carrying equipment with a moved space,
 * and face-drag resizing.
 *
 * The "Grid & snapping" page of the Settings window (settingsSections.tsx); the
 * Builder panel keeps a link to it (`GridSnappingLink`), and the
 * `core:builder-grid-settings` command opens it, for a toolbar.
 */

import React from "react";

import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {inputCls} from "./chrome";

export const GridSnappingOptions: React.FC = () => {
  const gridStep = useCellBuilderStore((s) => s.gridStep);
  const setGridStep = useCellBuilderStore((s) => s.setGridStep);
  const snapThreshold = useCellBuilderStore((s) => s.snapThreshold);
  const setSnapThreshold = useCellBuilderStore((s) => s.setSnapThreshold);
  const selectMode = useCellBuilderStore((s) => s.selectMode);
  const setSelectMode = useCellBuilderStore((s) => s.setSelectMode);
  const gizmoVertexSnap = useCellBuilderStore((s) => s.gizmoVertexSnap);
  const setGizmoVertexSnap = useCellBuilderStore((s) => s.setGizmoVertexSnap);
  const moveEquipWithCell = useCellBuilderStore((s) => s.moveEquipWithCell);
  const setMoveEquipWithCell = useCellBuilderStore((s) => s.setMoveEquipWithCell);
  const faceDragResize = useCellBuilderStore((s) => s.faceDragResize);
  const setFaceDragResize = useCellBuilderStore((s) => s.setFaceDragResize);
  return (
    <div className="flex flex-col gap-2 text-sm" data-grid-snapping-options="">
      <div className="flex items-center gap-x-3 gap-y-1 flex-wrap">
        <label className="flex items-center gap-1" title="The grid a placed, moved or resized box snaps to (m)">
          Grid step
          <input
            type="number"
            step={0.05}
            min={0}
            value={gridStep}
            onChange={(e) => setGridStep(Number(e.target.value))}
            className={`${inputCls} w-16`}
            data-option="builder-grid-step"
          />
        </label>
        <label className="flex items-center gap-1" title="How near a placed box comes to a neighbour's face before it snaps onto it (m)">
          Snap distance
          <input
            type="number"
            step={0.05}
            min={0}
            value={snapThreshold}
            onChange={(e) => setSnapThreshold(Number(e.target.value))}
            className={`${inputCls} w-16`}
            data-option="builder-snap-threshold"
          />
        </label>
      </div>
      <span
        className="flex items-center gap-0.5"
        title="What a plain click selects — explicit: the mode decides (cell / face / nearest border edge), no hover auto-pick"
      >
        <span className="mr-1">Select</span>
        {(["none", "cell", "face", "edge"] as const).map((m) => (
          <button
            key={m}
            type="button"
            className={
              "px-1.5 py-0.5 rounded-sm " +
              (selectMode === m ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300 hover:bg-gray-600")
            }
            onClick={() => setSelectMode(m)}
            aria-pressed={selectMode === m}
          >
            {m}
          </button>
        ))}
      </span>
      <label
        className="flex items-center gap-1"
        title="Vertex snapping: while moving a cell with the translate gizmo, magnetically align its corners onto neighbouring cells' corners (within the snap distance). With an axis lock (X/Y/Z) active, the snap is constrained to that axis only, Blender-style."
      >
        <input type="checkbox" checked={gizmoVertexSnap} onChange={(e) => setGizmoVertexSnap(e.target.checked)} />
        Vertex snap (move)
      </label>
      <label
        className="flex items-center gap-1"
        title="When you translate a space cell, carry the equipment sitting inside it along with the cell (rigid move). Turn off to move a cell without disturbing its equipment."
      >
        <input type="checkbox" checked={moveEquipWithCell} onChange={(e) => setMoveEquipWithCell(e.target.checked)} />
        Move equipment with cell
      </label>
      <label
        className="flex items-center gap-1"
        title="Allow dragging a cell face in the scene to resize it. Off by default — use the Resize gizmo (long-press / right-click a cell, or the selection panel) instead."
      >
        <input type="checkbox" checked={faceDragResize} onChange={(e) => setFaceDragResize(e.target.checked)} />
        Drag faces to resize
      </label>
    </div>
  );
};

/** The Builder panel's way to these settings: a line naming the grid step and a link that opens
 * the Settings window on its "Grid & snapping" page. */
export const GridSnappingLink: React.FC<{onOpen: () => void}> = ({onOpen}) => {
  const gridStep = useCellBuilderStore((s) => s.gridStep);
  const snapThreshold = useCellBuilderStore((s) => s.snapThreshold);
  return (
    <div className="flex items-center gap-2 text-[11px] text-gray-400">
      <span>
        grid {gridStep} · snap {snapThreshold}
      </span>
      <button
        type="button"
        className="ml-auto text-blue-300 hover:underline"
        title="Grid step, snap distance, select mode, vertex snap and the move / resize options are in Settings"
        onClick={onOpen}
      >
        Grid & snapping… opens Settings
      </button>
    </div>
  );
};
