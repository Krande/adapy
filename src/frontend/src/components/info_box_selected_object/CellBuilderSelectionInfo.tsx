import React from "react";

import {
  useCellBuilderStore,
  type BuilderCell,
  type BuilderSelection,
} from "@/state/cellBuilderStore";
import { axisLabel, BOX_FACE_SIDES } from "@/utils/cellbuilder/snap";

// The selected cell/equipment detail shown in the Selected Object Info panel:
// gizmo toggles, the geometry/parameter editors mirrored from the ada.topology
// pydantic entities, and the connected-systems list, each collapsible. Shows
// only while a procedural model has a selection. Model-wide actions (undo/redo,
// commit/compile, visibility toggles) live solely in the dedicated cellbuilder
// tool panel (CellBuilderPanel), not here.

const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
const inputCls =
  "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";

// Editable per-cell parameters, mirrored from ada/topology/entities.py
// (TopoSpace / TopoEquipment). Anything not listed still round-trips
// untouched through BuilderCell.params.
type ParamField =
  | { key: string; label: string; type: "bool" }
  | { key: string; label: string; type: "number"; step?: number }
  | { key: string; label: string; type: "text" }
  | { key: string; label: string; type: "select"; options: string[] };

const SPACE_PARAMS: ParamField[] = [
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

const EQUIPMENT_PARAMS: ParamField[] = [
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

const SYSTEM_TYPE_COLOR: Record<string, string> = {
  piping: "#38bdf8",
  duct: "#a3e635",
  cable: "#c084fc",
  electrical: "#f59e0b",
};

const ParamRow: React.FC<{ cell: BuilderCell; field: ParamField }> = ({
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

// Systems this equipment participates in — which system it's connected to, via
// which port. Its own collapsible section.
const PORT_CATEGORY_COLOR: Record<string, string> = {
  process: "#38bdf8",
  electrical: "#f59e0b",
  signal: "#ec4899",
};

const EquipmentSystems: React.FC<{
  equipmentName: string;
  equipmentType?: string;
}> = ({ equipmentName, equipmentType }) => {
  const systems = useCellBuilderStore((st) => st.systems);
  const equipmentTypes = useCellBuilderStore((st) => st.equipmentTypes);
  const [open, setOpen] = React.useState(true);
  const connected = Object.values(systems).filter((sys) =>
    sys.connections.some((c) => c.equipment === equipmentName),
  );

  // This equipment's type ports, and which are wired up by a system — so the
  // unconnected I/O (what the red "!" overlay flags) can be listed explicitly.
  const ports = React.useMemo(() => {
    if (!equipmentType) return [];
    const key = equipmentType.toLowerCase();
    const t =
      equipmentTypes.find((o) => o.slug.toLowerCase() === key) ??
      equipmentTypes.find((o) => o.name.toLowerCase() === key);
    return t?.ports ?? [];
  }, [equipmentType, equipmentTypes]);
  const connectedPorts = new Set<string>();
  for (const sys of Object.values(systems))
    for (const c of sys.connections)
      if (c.equipment === equipmentName && c.port) connectedPorts.add(c.port);
  const unconnected = ports.filter((p) => !connectedPorts.has(p.name));

  return (
    <div className="border-t border-gray-600/60 pt-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-gray-700/40 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>
          ▸
        </span>
        <span className="font-semibold">Systems & I/O</span>
        <span className="text-gray-400">({connected.length} wired)</span>
        {unconnected.some((p) => p.direction === "IN") && (
          <span
            className="ml-1 text-red-400"
            title="Has unconnected input(s)"
          >
            ⚠
          </span>
        )}
      </button>
      {open && (
        <div className="flex flex-col gap-1 px-1 pt-1">
          {connected.length === 0 ? (
            <div className="text-gray-500 italic">
              Not connected to any system.
            </div>
          ) : (
            <div className="flex flex-col gap-0.5">
              {connected.map((sys) => {
                const cports = sys.connections
                  .filter((c) => c.equipment === equipmentName)
                  .map((c) => c.port);
                return (
                  <div key={sys.id} className="flex items-center gap-1">
                    <span
                      className="inline-block w-2 h-2 rounded-full"
                      style={{ background: SYSTEM_TYPE_COLOR[sys.type] }}
                    />
                    <span className="truncate">{sys.name}</span>
                    <span className="text-gray-400">({sys.type})</span>
                    <span className="ml-auto text-gray-400">
                      {cports.join(", ")}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          {/* Unconnected ports — inputs (IN) are the "missing" ones the overlay
              warns about; outputs/signals are shown too but not flagged. */}
          {unconnected.length > 0 && (
            <div className="flex flex-col gap-0.5 border-t border-gray-700/50 pt-1">
              <span className="text-gray-400">Unconnected I/O</span>
              {unconnected.map((p) => (
                <div key={p.name} className="flex items-center gap-1">
                  <span
                    className="inline-block w-2 h-2 rounded-full"
                    style={{ background: PORT_CATEGORY_COLOR[p.category] }}
                  />
                  <span
                    className={
                      "truncate " + (p.direction === "IN" ? "text-red-300" : "")
                    }
                  >
                    {p.name}
                  </span>
                  <span className="ml-auto text-gray-500">
                    {p.direction} · {p.category}
                    {p.direction === "IN" ? " · missing" : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
          {equipmentType && ports.length === 0 && (
            <div className="text-gray-600 italic">
              Port list unavailable (type not loaded).
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const SelectionSection: React.FC<{ selection: BuilderSelection }> = ({
  selection,
}) => {
  const cells = useCellBuilderStore((s) => s.cells);
  const setSelection = useCellBuilderStore((s) => s.setSelection);
  const applyFaceExtension = useCellBuilderStore((s) => s.applyFaceExtension);
  const setEdgeLength = useCellBuilderStore((s) => s.setEdgeLength);
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const renameCell = useCellBuilderStore((s) => s.renameCell);
  const gizmoMode = useCellBuilderStore((s) => s.gizmoMode);
  const setGizmoMode = useCellBuilderStore((s) => s.setGizmoMode);
  const insertOpeningOnFace = useCellBuilderStore((s) => s.insertOpeningOnFace);
  const setCellEnclosed = useCellBuilderStore((s) => s.setCellEnclosed);
  const enclosedCells = useCellBuilderStore(
    (s) =>
      (s.blueprintOptions as { enclosed_cells?: string[] }).enclosed_cells ?? [],
  );
  const [open, setOpen] = React.useState(true);
  const [extendBy, setExtendBy] = React.useState(0.5);
  // Deliberately no re-open on a new pick: clicking another cell must not
  // force-expand a section the user has collapsed. The collapse state persists.

  const cell = cells[selection.cellId];
  if (!cell) return null;

  const side =
    selection.kind === "face" && selection.faceIndex !== undefined
      ? BOX_FACE_SIDES[selection.faceIndex]
      : null;
  const edgeAxis = selection.edge?.axis;
  const title =
    selection.kind === "cell"
      ? `Cell ${cell.name}`
      : selection.kind === "face"
        ? `Face ${side?.label ?? "?"} of ${cell.name}`
        : `Edge along ${axisLabel(edgeAxis ?? 0)} of ${cell.name}`;

  return (
    <div className="border-t border-gray-600/60 pt-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-gray-700/40 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>
          ▸
        </span>
        <span className="font-semibold truncate">{title}</span>
        <span
          className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
          title="Clear selection (Esc)"
          onClick={(e) => {
            e.stopPropagation();
            setSelection(null);
          }}
        >
          ✕
        </span>
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 px-1 pt-1">
          <div
            className="flex items-center gap-1"
            title="Direct-manipulation gizmo for this cell (also via long-press / right-click in the scene)"
          >
            <span className="text-gray-300">gizmo</span>
            {(
              // Equipment is sized by its type — offer Move + Rotate, no Resize.
              cell.kind === "cell"
                ? ([
                    ["translate", "Move"],
                    ["resize", "Resize"],
                    ["none", "Off"],
                  ] as const)
                : ([
                    ["translate", "Move"],
                    ["rotate", "Rotate"],
                    ["none", "Off"],
                  ] as const)
            ).map(([m, label]) => (
              <button
                key={m}
                className={
                  "px-1.5 py-0.5 rounded-sm " +
                  (gizmoMode === m
                    ? "bg-blue-600 text-white"
                    : "bg-gray-700 text-gray-300 hover:bg-gray-600")
                }
                onClick={() => setGizmoMode(m)}
                aria-pressed={gizmoMode === m}
              >
                {label}
              </button>
            ))}
          </div>
          {selection.kind === "face" && side && (
            <>
              {/* Face-extend resizes the box — cells only; equipment is sized
                  by its type. */}
              {cell.kind === "cell" && (
                <div className="flex items-center gap-1">
                  <span className="text-gray-300">Extend by</span>
                  <input
                    type="number"
                    step={0.1}
                    className={`${inputCls} w-20`}
                    value={extendBy}
                    onChange={(e) => setExtendBy(Number(e.target.value))}
                  />
                  <button
                    className={btn}
                    onClick={() =>
                      applyFaceExtension(cell.id, selection.faceIndex!, extendBy)
                    }
                    title="Extend (negative contracts) this face outward"
                  >
                    Apply
                  </button>
                </div>
              )}
              {cell.kind === "cell" && (
                <label
                  className="flex items-center gap-1"
                  title={`TopoSpace side exclusion SE${side.se} — omit this side when building`}
                >
                  <input
                    type="checkbox"
                    checked={Boolean(cell.params[`SE${side.se}`])}
                    onChange={(e) =>
                      setCellParam(
                        cell.id,
                        `SE${side.se}`,
                        e.target.checked || null,
                      )
                    }
                  />
                  Exclude side (SE{side.se})
                </label>
              )}
              {cell.kind === "cell" && (
                <div className="flex items-center gap-1">
                  <span className="text-gray-300">Insert opening</span>
                  <button
                    className={btn}
                    onClick={() =>
                      insertOpeningOnFace(cell.id, selection.faceIndex!, "door")
                    }
                    title="Add a door opening straddling this face (0.9 × 2.1 m, at the floor)"
                  >
                    + Door
                  </button>
                  <button
                    className={btn}
                    onClick={() =>
                      insertOpeningOnFace(
                        cell.id,
                        selection.faceIndex!,
                        "window",
                      )
                    }
                    title="Add a window opening straddling this face (1.2 × 1.0 m, 1.0 m sill)"
                  >
                    + Window
                  </button>
                </div>
              )}
            </>
          )}
          {selection.kind === "edge" && edgeAxis !== undefined && cell.kind === "cell" && (
            <div className="flex items-center gap-1">
              <span className="text-gray-300">Length {axisLabel(edgeAxis)}</span>
              <input
                type="number"
                step={0.1}
                min={0.1}
                className={`${inputCls} w-20`}
                value={cell.size[edgeAxis]}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (v > 0) setEdgeLength(cell.id, edgeAxis, v);
                }}
              />
            </div>
          )}
          {selection.kind === "cell" && (
            <div className="flex flex-col gap-1">
              <label className="flex items-center gap-1">
                <span className="text-gray-300">name</span>
                <input
                  type="text"
                  className={`${inputCls} flex-1 min-w-0`}
                  defaultValue={cell.name}
                  key={cell.id + cell.name}
                  onBlur={(e) => renameCell(cell.id, e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") e.currentTarget.blur();
                  }}
                  title={
                    cell.kind === "equipment"
                      ? "Renaming rewrites this equipment's system connections too"
                      : "Cell name"
                  }
                />
              </label>
              <div className="text-gray-400">
                origin {cell.origin.map((v) => v.toFixed(2)).join(", ")} · size{" "}
                {cell.size.map((v) => v.toFixed(2)).join(", ")}
              </div>
              {cell.kind === "cell" && (
                <label
                  className="flex items-center gap-1"
                  title="Plate every bounding face of this cell (walls + floor/roof decks, stiffeners facing inward) so the room is fully enclosed"
                >
                  <input
                    type="checkbox"
                    checked={enclosedCells.includes(cell.name)}
                    onChange={(e) => setCellEnclosed(cell.name, e.target.checked)}
                  />
                  Enclosed room (plated walls)
                </label>
              )}
              {(cell.kind === "cell" ? SPACE_PARAMS : EQUIPMENT_PARAMS).map(
                (f) => (
                  <ParamRow key={f.key} cell={cell} field={f} />
                ),
              )}
            </div>
          )}
          {cell.kind === "equipment" && (
            <EquipmentSystems
              equipmentName={cell.name}
              equipmentType={cell.equipmentType}
            />
          )}
        </div>
      )}
    </div>
  );
};

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
