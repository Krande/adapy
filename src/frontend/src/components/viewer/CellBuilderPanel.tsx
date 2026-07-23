import React from "react";

import {useCellBuilderStore, type BuilderCell, type BuilderSelection} from "@/state/cellBuilderStore";
import {axisLabel, BOX_FACE_SIDES} from "@/utils/cellbuilder/snap";

// The procedural-modelling context panel: add cells / typed equipment, list +
// edit the boxes, a collapsible selection section (cell/face/edge parameters
// mirrored from the ada.topology pydantic entities), commit to postgres
// (revision-tracked) and compile via the worker. Toggled from its own top-row
// button in Menu (only rendered while a procedural model is loaded).

const btn = "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
const btnGray = "px-2 py-1 rounded-sm bg-gray-600 text-white disabled:opacity-50 hover:bg-gray-500";
const inputCls = "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";

// Editable per-cell parameters, mirrored from ada/topology/entities.py
// (TopoSpace / TopoEquipment). Anything not listed still round-trips
// untouched through BuilderCell.params.
type ParamField =
    | {key: string; label: string; type: "bool"}
    | {key: string; label: string; type: "number"; step?: number}
    | {key: string; label: string; type: "text"}
    | {key: string; label: string; type: "select"; options: string[]};

const SPACE_PARAMS: ParamField[] = [
    {key: "AREA", label: "Area", type: "text"},
    {key: "PRIORITY", label: "Priority", type: "number", step: 1},
    {key: "FLIP_FLOOR", label: "Flip floor", type: "bool"},
    {key: "GRID_X_CREATE", label: "Grid X", type: "bool"},
    {key: "GRID_Y_CREATE", label: "Grid Y", type: "bool"},
    {key: "GRID_Z_CREATE", label: "Grid Z", type: "bool"},
    {key: "SWITCH_BM_DIR_VERTICAL", label: "Switch vert. beams", type: "bool"},
    {key: "SWITCH_BM_DIR_HORIZONTAL", label: "Switch horiz. beams", type: "bool"},
];

const EQUIPMENT_PARAMS: ParamField[] = [
    {key: "SPACE_LOC", label: "Location", type: "select", options: ["FLOOR", "ROOF"]},
    {key: "massDry", label: "Mass dry [kg]", type: "number", step: 100},
    {key: "massCont", label: "Mass content [kg]", type: "number", step: 100},
    {key: "COGx", label: "COG x", type: "number", step: 0.1},
    {key: "COGy", label: "COG y", type: "number", step: 0.1},
    {key: "COGz", label: "COG z", type: "number", step: 0.1},
];

const ParamRow: React.FC<{cell: BuilderCell; field: ParamField}> = ({cell, field}) => {
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
                    onChange={(e) => setCellParam(cell.id, field.key, e.target.value || null)}
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
                        setCellParam(cell.id, field.key, e.target.value === "" ? null : Number(e.target.value))
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
                onChange={(e) => setCellParam(cell.id, field.key, e.target.value || null)}
            />
        </label>
    );
};

const SelectionSection: React.FC<{selection: BuilderSelection}> = ({selection}) => {
    const cells = useCellBuilderStore((s) => s.cells);
    const setSelection = useCellBuilderStore((s) => s.setSelection);
    const applyFaceExtension = useCellBuilderStore((s) => s.applyFaceExtension);
    const setEdgeLength = useCellBuilderStore((s) => s.setEdgeLength);
    const setCellParam = useCellBuilderStore((s) => s.setCellParam);
    const [open, setOpen] = React.useState(true);
    const [extendBy, setExtendBy] = React.useState(0.5);
    // Re-open on a new pick so the panel always reveals what was just clicked.
    React.useEffect(() => setOpen(true), [selection]);

    const cell = cells[selection.cellId];
    if (!cell) return null;

    const side = selection.kind === "face" && selection.faceIndex !== undefined ? BOX_FACE_SIDES[selection.faceIndex] : null;
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
                <span className={"transition-transform " + (open ? "rotate-90" : "")}>▸</span>
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
                                    onClick={() => applyFaceExtension(cell.id, selection.faceIndex!, extendBy)}
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
                                        onChange={(e) => setCellParam(cell.id, `SE${side.se}`, e.target.checked || null)}
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
                            <div className="text-gray-400">
                                origin {cell.origin.map((v) => v.toFixed(2)).join(", ")} · size{" "}
                                {cell.size.map((v) => v.toFixed(2)).join(", ")}
                            </div>
                            {(cell.kind === "cell" ? SPACE_PARAMS : EQUIPMENT_PARAMS).map((f) => (
                                <ParamRow key={f.key} cell={cell} field={f} />
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

const CellBuilderPanel: React.FC = () => {
    const s = useCellBuilderStore();

    if (!s.active || !s.panelVisible) return null;

    const compileState = s.compileJob;
    const compileBusy = compileState != null && (compileState.status === "queued" || compileState.status === "running");
    const resultReady = compileState != null && (compileState.status === "done" || compileState.status === "cached");

    return (
        <div className="flex flex-col gap-2 text-xs text-white p-2 bg-gray-900/70 rounded-md min-w-[300px] max-w-[380px] pointer-events-auto">
            <div className="flex items-center gap-2">
                <span className="font-semibold truncate" title={s.active.modelId}>
                    {s.active.name}
                </span>
                <span className="text-gray-400">r{s.active.revision}</span>
                {s.dirty && <span className="text-amber-400">● unsaved</span>}
                <button className="ml-auto px-1 rounded-sm hover:bg-gray-500/40" title="Close model" onClick={s.close}>
                    ✕
                </button>
            </div>

            <div className="flex items-center gap-1 flex-wrap">
                <button
                    className={s.mode === "add-cell" ? `${btn} ring-2 ring-blue-300` : btn}
                    onClick={() => s.setMode(s.mode === "add-cell" ? "idle" : "add-cell")}
                    title="Click in the scene to place a cell (Esc cancels)"
                >
                    + Cell
                </button>
                <button
                    className={s.mode === "add-equipment" ? `${btn} ring-2 ring-blue-300` : btn}
                    disabled={s.equipmentTypes.length === 0 && s.selectedEquipmentType === null}
                    onClick={() => s.setMode(s.mode === "add-equipment" ? "idle" : "add-equipment")}
                    title="Click in the scene to place equipment (Esc cancels)"
                >
                    + Equipment
                </button>
                <select
                    className={inputCls}
                    value={s.selectedEquipmentType ?? ""}
                    onChange={(e) => s.setSelectedEquipmentType(e.target.value || null)}
                    title="Equipment archetype (advertised by the worker pool for this scope)"
                >
                    {s.equipmentTypes.length === 0 && <option value="">no types advertised</option>}
                    {s.equipmentTypes.map((t) => (
                        <option key={t} value={t}>
                            {t}
                        </option>
                    ))}
                </select>
            </div>

            <div className="flex items-center gap-2">
                <label className="flex items-center gap-1">
                    grid
                    <input
                        type="number"
                        step={0.05}
                        min={0}
                        value={s.gridStep}
                        onChange={(e) => s.setGridStep(Number(e.target.value))}
                        className={`${inputCls} w-14`}
                    />
                </label>
                <label className="flex items-center gap-1">
                    snap
                    <input
                        type="number"
                        step={0.05}
                        min={0}
                        value={s.snapThreshold}
                        onChange={(e) => s.setSnapThreshold(Number(e.target.value))}
                        className={`${inputCls} w-14`}
                    />
                </label>
                <span className="flex items-center gap-0.5" title="What a plain click selects (border clicks always pick the edge)">
                    <span className="text-gray-300">select</span>
                    {(["cell", "face"] as const).map((m) => (
                        <button
                            key={m}
                            className={
                                "px-1.5 py-0.5 rounded-sm " +
                                (s.selectMode === m ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300 hover:bg-gray-600")
                            }
                            onClick={() => s.setSelectMode(m)}
                            aria-pressed={s.selectMode === m}
                        >
                            {m}
                        </button>
                    ))}
                </span>
                <label className="flex items-center gap-1 ml-auto" title="Compile automatically after each commit">
                    <input type="checkbox" checked={s.autoCompile} onChange={(e) => s.setAutoCompile(e.target.checked)} />
                    auto-compile
                </label>
            </div>

            <div className="max-h-48 overflow-y-auto flex flex-col gap-1">
                {Object.values(s.cells).length === 0 && (
                    <p className="italic text-gray-400">No cells yet — use + Cell to start.</p>
                )}
                {Object.values(s.cells).map((c) => (
                    <div
                        key={c.id}
                        className={
                            "flex items-center gap-1 border-b border-gray-600/40 pb-0.5 cursor-pointer rounded-sm px-0.5 " +
                            (s.selection?.cellId === c.id ? "bg-blue-900/40" : "hover:bg-gray-700/40")
                        }
                        onClick={() => s.setSelection({kind: "cell", cellId: c.id})}
                    >
                        <span
                            className="inline-block w-2 h-2 rounded-sm"
                            style={{background: c.kind === "cell" ? "#3b82f6" : "#f97316"}}
                        />
                        <span className="truncate" title={`${c.origin.map((v) => v.toFixed(2))} / ${c.size.map((v) => v.toFixed(2))}`}>
                            {c.name}
                        </span>
                        {c.kind === "equipment" && <span className="text-gray-400">{c.equipmentType ?? "generic"}</span>}
                        <button
                            className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
                            title="Delete"
                            onClick={(e) => {
                                e.stopPropagation();
                                s.removeCell(c.id);
                            }}
                        >
                            🗑
                        </button>
                    </div>
                ))}
            </div>

            {s.selection && <SelectionSection selection={s.selection} />}

            {s.conflict && <p className="text-red-400">{s.conflict}</p>}
            {compileState?.status === "error" && <p className="text-red-400">Compile failed: {compileState.error}</p>}

            <div className="flex items-center gap-1 flex-wrap">
                <button className={btn} disabled={!s.dirty || s.committing} onClick={() => void s.commit()}>
                    {s.committing ? "Committing…" : "Commit"}
                </button>
                <button className={btnGray} disabled={compileBusy} onClick={() => void s.compile()}>
                    {compileBusy ? `Compiling (${compileState?.status})…` : "Compile"}
                </button>
                {resultReady && compileState && s.resultSourceName === null && (
                    <button className={btnGray} onClick={() => void s.viewResult(compileState.derivedKey)} title={compileState.derivedKey}>
                        View result
                    </button>
                )}
                {s.resultSourceName !== null && (
                    <button className={btnGray} onClick={s.hideResult} title="Unload the compiled result from the scene">
                        Hide result
                    </button>
                )}
                <button
                    className={btnGray}
                    onClick={() => s.setCellsVisible(!s.cellsVisible)}
                    title="Toggle the builder cell boxes (hide to focus on the generated structure)"
                    aria-pressed={!s.cellsVisible}
                >
                    {s.cellsVisible ? "Hide cells" : "Show cells"}
                </button>
            </div>
        </div>
    );
};

export default CellBuilderPanel;
