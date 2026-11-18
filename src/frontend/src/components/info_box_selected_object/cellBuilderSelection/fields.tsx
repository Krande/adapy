// The parameter editors of the cell builder's selection detail: the shared control classes,
// the space and equipment parameter lists, and one parameter's row.

import React from "react";
import { useCellBuilderStore, type BuilderCell } from "@/state/cellBuilderStore";

export const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
export const inputCls =
  "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";

// Editable per-cell parameters, mirrored from ada/topology/entities.py
// (TopoSpace / TopoEquipment). Anything not listed still round-trips
// untouched through BuilderCell.params.
type ParamField =
  | { key: string; label: string; type: "bool" }
  | { key: string; label: string; type: "number"; step?: number }
  | { key: string; label: string; type: "text" }
  | { key: string; label: string; type: "select"; options: string[] };

export const SPACE_PARAMS: ParamField[] = [
  { key: "AREA", label: "Area", type: "text" },
  { key: "PRIORITY", label: "Priority", type: "number", step: 1 },
  { key: "FLIP_FLOOR", label: "Flip floor", type: "bool" },
  { key: "GRID_X_CREATE", label: "Grid X", type: "bool" },
  { key: "GRID_Y_CREATE", label: "Grid Y", type: "bool" },
  { key: "GRID_Z_CREATE", label: "Grid Z", type: "bool" },
  { key: "SWITCH_BM_DIR_VERTICAL", label: "Switch vert. beams", type: "bool" },
  {
    key: "SWITCH_BM_DIR_HORIZONTAL",
    label: "Switch horiz. beams",
    type: "bool",
  },
];

export const EQUIPMENT_PARAMS: ParamField[] = [
  {
    key: "SPACE_LOC",
    label: "Location",
    type: "select",
    options: ["FLOOR", "ROOF"],
  },
  { key: "massDry", label: "Mass dry [kg]", type: "number", step: 100 },
  { key: "massCont", label: "Mass content [kg]", type: "number", step: 100 },
  { key: "COGx", label: "COG x", type: "number", step: 0.1 },
  { key: "COGy", label: "COG y", type: "number", step: 0.1 },
  { key: "COGz", label: "COG z", type: "number", step: 0.1 },
];

export const ParamRow: React.FC<{ cell: BuilderCell; field: ParamField }> = ({
  cell,
  field,
}) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const value = cell.params[field.key];
  if (field.type === "bool") {
    return (
      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => setCellParam(cell.id, field.key, e.target.checked)}
        />
        {field.label}
      </label>
    );
  }
  if (field.type === "select") {
    return (
      <label className="flex items-center gap-1">
        <span className="text-gray-300">{field.label}</span>
        <select
          className={inputCls}
          value={typeof value === "string" ? value : ""}
          onChange={(e) =>
            setCellParam(cell.id, field.key, e.target.value || null)
          }
        >
          <option value=""></option>
          {field.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (field.type === "number") {
    return (
      <label className="flex items-center gap-1">
        <span className="text-gray-300">{field.label}</span>
        <input
          type="number"
          step={field.step ?? 0.1}
          className={`${inputCls} w-20`}
          value={typeof value === "number" ? value : ""}
          onChange={(e) =>
            setCellParam(
              cell.id,
              field.key,
              e.target.value === "" ? null : Number(e.target.value),
            )
          }
        />
      </label>
    );
  }
  return (
    <label className="flex items-center gap-1">
      <span className="text-gray-300">{field.label}</span>
      <input
        type="text"
        className={`${inputCls} w-24`}
        value={typeof value === "string" ? value : ""}
        onChange={(e) =>
          setCellParam(cell.id, field.key, e.target.value || null)
        }
      />
    </label>
  );
};
