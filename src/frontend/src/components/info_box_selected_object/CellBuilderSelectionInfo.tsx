import React from "react";

import {
  useCellBuilderStore,
  type BuilderCell,
  type BuilderSelection,
} from "@/state/cellBuilderStore";
import { axisLabel, BOX_FACE_SIDES } from "@/utils/cellbuilder/snap";

// The selected cell/equipment detail — moved out of the (already busy)
// procedural tool panel into the Selected Object Info panel. Renders the active
// gizmo toggles, the geometry/parameter editors mirrored from the ada.topology
// pydantic entities, and the connected-systems list, each in its own
// collapsible section. Shows only while a procedural model has a selection.

const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
// Compact touch-target button for the model-wide toggle row.
const smallBtn =
  "px-2 py-1 rounded-sm bg-gray-700 text-gray-200 hover:bg-gray-600 disabled:opacity-40";
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
const EquipmentSystems: React.FC<{ equipmentName: string }> = ({
  equipmentName,
}) => {
  const systems = useCellBuilderStore((st) => st.systems);
  const [open, setOpen] = React.useState(true);
  const connected = Object.values(systems).filter((sys) =>
    sys.connections.some((c) => c.equipment === equipmentName),
  );
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
        <span className="font-semibold">Connected systems</span>
        <span className="text-gray-400">({connected.length})</span>
      </button>
      {open &&
        (connected.length === 0 ? (
          <div className="text-gray-500 italic px-1 pt-1">
            Not connected to any system.
          </div>
        ) : (
          <div className="flex flex-col gap-0.5 px-1 pt-1">
            {connected.map((sys) => {
              const ports = sys.connections
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
                    {ports.join(", ")}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
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
  const [open, setOpen] = React.useState(true);
  const [extendBy, setExtendBy] = React.useState(0.5);
  // Re-open on a new pick so the panel always reveals what was just clicked.
  React.useEffect(() => setOpen(true), [selection]);

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
              [
                ["translate", "Move"],
                ["resize", "Resize"],
                ["none", "Off"],
              ] as const
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
            </>
          )}
          {selection.kind === "edge" && edgeAxis !== undefined && (
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
              {(cell.kind === "cell" ? SPACE_PARAMS : EQUIPMENT_PARAMS).map(
                (f) => (
                  <ParamRow key={f.key} cell={cell} field={f} />
                ),
              )}
            </div>
          )}
          {cell.kind === "equipment" && (
            <EquipmentSystems equipmentName={cell.name} />
          )}
        </div>
      )}
    </div>
  );
};

// Model-wide controls duplicated here so the mobile UI can drive the whole
// procedural model from the Selected Object Info panel without reaching for the
// (desktop-oriented) tool panel: model name/revision, undo/redo, the visibility
// toggles (cells / ports / compiled result) and commit/compile.
const ModelActions: React.FC = () => {
  const s = useCellBuilderStore();
  const compileState = s.compileJob;
  const compileBusy =
    compileState != null &&
    (compileState.status === "queued" || compileState.status === "running");
  const resultReady =
    compileState != null &&
    (compileState.status === "done" || compileState.status === "cached");
  const [open, setOpen] = React.useState(true);
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
        <span className="font-semibold truncate" title={s.active?.modelId}>
          {s.active?.name}
        </span>
        <span className="text-gray-400">r{s.active?.revision}</span>
        {s.dirty && <span className="text-amber-400">● unsaved</span>}
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 px-1 pt-1">
          <div className="flex items-center gap-1 flex-wrap">
            <button
              className={smallBtn}
              disabled={s.past.length === 0}
              onClick={s.undo}
              title="Undo (Ctrl+Z)"
            >
              ↶ Undo
            </button>
            <button
              className={smallBtn}
              disabled={s.future.length === 0}
              onClick={s.redo}
              title="Redo (Ctrl+Shift+Z)"
            >
              ↷ Redo
            </button>
            <button
              className={smallBtn}
              onClick={() => s.setCellsVisible(!s.cellsVisible)}
              aria-pressed={!s.cellsVisible}
              title="Toggle the builder cell boxes"
            >
              {s.cellsVisible ? "Hide cells" : "Show cells"}
            </button>
            <button
              className={smallBtn}
              onClick={() => s.setPortsOverlayVisible(!s.portsOverlayVisible)}
              aria-pressed={s.portsOverlayVisible}
              title="Toggle the equipment port overlay"
            >
              {s.portsOverlayVisible ? "Hide ports" : "Show ports"}
            </button>
            {s.resultSourceName !== null ? (
              <button
                className={smallBtn}
                onClick={s.hideResult}
                title="Unload the compiled result from the scene"
              >
                Hide result
              </button>
            ) : (
              resultReady &&
              compileState && (
                <button
                  className={smallBtn}
                  onClick={() => void s.viewResult(compileState.derivedKey)}
                  title={compileState.derivedKey}
                >
                  View result
                </button>
              )
            )}
          </div>
          {s.conflict && <p className="text-red-400">{s.conflict}</p>}
          {compileState?.status === "error" && (
            <p className="text-red-400">
              Compile failed: {compileState.error}
            </p>
          )}
          <div className="flex items-center gap-1 flex-wrap">
            <button
              className={btn}
              disabled={!s.dirty || s.committing}
              onClick={() => void s.commit()}
            >
              {s.committing ? "Committing…" : "Commit"}
            </button>
            <button
              className={
                "px-2 py-1 rounded-sm bg-gray-600 text-white disabled:opacity-50 hover:bg-gray-500"
              }
              disabled={compileBusy}
              onClick={() => void s.compile()}
            >
              {compileBusy ? `Compiling (${compileState?.status})…` : "Compile"}
            </button>
          </div>
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
      <ModelActions />
    </div>
  );
};

export default CellBuilderSelectionInfo;
