import React from "react";

import {useCellBuilderStore} from "@/state/cellBuilderStore";

// The procedural-modelling context panel: add cells / typed equipment, list +
// edit the boxes, commit to postgres (revision-tracked) and compile via the
// worker. Toggled from its own top-row button in Menu (only rendered while a
// procedural model is loaded).

const btn = "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
const btnGray = "px-2 py-1 rounded-sm bg-gray-600 text-white disabled:opacity-50 hover:bg-gray-500";

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
                    className="text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5"
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
                        className="w-14 text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1"
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
                        className="w-14 text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1"
                    />
                </label>
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
                    <div key={c.id} className="flex items-center gap-1 border-b border-gray-600/40 pb-0.5">
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
                            onClick={() => s.removeCell(c.id)}
                        >
                            🗑
                        </button>
                    </div>
                ))}
            </div>

            {s.conflict && <p className="text-red-400">{s.conflict}</p>}
            {compileState?.status === "error" && <p className="text-red-400">Compile failed: {compileState.error}</p>}

            <div className="flex items-center gap-1">
                <button className={btn} disabled={!s.dirty || s.committing} onClick={() => void s.commit()}>
                    {s.committing ? "Committing…" : "Commit"}
                </button>
                <button className={btnGray} disabled={compileBusy} onClick={() => void s.compile()}>
                    {compileBusy ? `Compiling (${compileState?.status})…` : "Compile"}
                </button>
                {resultReady && compileState && (
                    <button className={btnGray} onClick={() => void s.viewResult(compileState.derivedKey)} title={compileState.derivedKey}>
                        View result
                    </button>
                )}
            </div>
        </div>
    );
};

export default CellBuilderPanel;
