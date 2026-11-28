import React, {useState} from "react";

import {
    ENGINE_LABELS,
    EXEC_OPERATIONS,
    OPERATION_LABELS,
    QUALIFIER_HINTS,
    WILDCARD,
    type ExecEngine,
    type ExecOperation,
} from "@/services/compute/executionPolicyModel";
import {
    addRow,
    missingEngines,
    moveEngine,
    removeRow,
    toggleEngine,
    updateRow,
    type PolicyRow,
} from "./executionPolicyEditor";

// An editable execution-policy table: operations x qualifier rows, each an
// ordered list of engine chips (move left/right, remove, add a missing one), plus
// adding/removing qualifier rows. Shared by the global Execution tab and the
// per-user override editor (where "*" rows may be removed too: an override only
// carries the rows it changes).

const ENGINE_CHIP: Record<ExecEngine, string> = {
    native: "bg-purple-900/50 border-purple-700 text-purple-100",
    pyodide: "bg-blue-900/50 border-blue-700 text-blue-100",
    worker: "bg-emerald-900/50 border-emerald-700 text-emerald-100",
};

const ExecutionPolicyTable: React.FC<{
    rows: PolicyRow[];
    onChange: (rows: PolicyRow[]) => void;
    disabled?: boolean;
    /** The global document keeps its "*" rows; an override does not have to. */
    keepWildcard?: boolean;
    /** Engine order a new row starts with (default: what the table answers now). */
    initialFor?: (op: ExecOperation, qualifier: string) => ExecEngine[] | undefined;
    testId?: string;
}> = ({rows, onChange, disabled = false, keepWildcard = true, initialFor, testId}) => {
    const [newOp, setNewOp] = useState<ExecOperation>("read");
    const [newQualifier, setNewQualifier] = useState("");
    const [addError, setAddError] = useState<string | null>(null);

    const add = () => {
        const q = newQualifier.trim();
        if (!q) return;
        const res = addRow(rows, newOp, q, initialFor?.(newOp, q.toLowerCase()));
        if ("error" in res) {
            setAddError(res.error);
            return;
        }
        setAddError(null);
        setNewQualifier("");
        onChange(res.rows);
    };

    return (
        <div className="space-y-2" data-testid={testId}>
            <table className="w-full text-xs border-collapse">
                <thead>
                    <tr className="text-left text-gray-400">
                        <th className="py-1 pr-2 font-normal">Operation</th>
                        <th className="py-1 pr-2 font-normal">For</th>
                        <th className="py-1 pr-2 font-normal">Engines, in order</th>
                        <th className="py-1 font-normal"/>
                    </tr>
                </thead>
                <tbody>
                    {rows.length === 0 && (
                        <tr>
                            <td colSpan={4} className="py-2 text-gray-500">
                                No rows.
                            </td>
                        </tr>
                    )}
                    {rows.map((row, i) => {
                        const firstOfOp = i === 0 || rows[i - 1].op !== row.op;
                        const set = (engines: ExecEngine[]) => onChange(updateRow(rows, row.op, row.qualifier, engines));
                        return (
                            <tr
                                key={`${row.op}:${row.qualifier}`}
                                className={"align-top " + (firstOfOp ? "border-t border-gray-800" : "")}
                                data-testid={`exec-row-${row.op}-${row.qualifier}`}
                            >
                                <td className="py-1 pr-2 text-gray-300 whitespace-nowrap">
                                    {firstOfOp ? OPERATION_LABELS[row.op] : ""}
                                </td>
                                <td className="py-1 pr-2 font-mono whitespace-nowrap">
                                    {row.qualifier === WILDCARD ? <span className="text-gray-400">any</span> : row.qualifier}
                                </td>
                                <td className="py-1 pr-2">
                                    <div className="flex flex-wrap items-center gap-1">
                                        {row.engines.map((engine, j) => (
                                            <span
                                                key={engine}
                                                className={`inline-flex items-center gap-0.5 border rounded-sm px-1 py-0.5 ${ENGINE_CHIP[engine]}`}
                                            >
                                                <span className="text-[10px] text-gray-400 mr-0.5">{j + 1}</span>
                                                {ENGINE_LABELS[engine]}
                                                <ChipButton
                                                    label="earlier"
                                                    disabled={disabled || j === 0}
                                                    onClick={() => set(moveEngine(row.engines, j, -1))}
                                                >
                                                    ‹
                                                </ChipButton>
                                                <ChipButton
                                                    label="later"
                                                    disabled={disabled || j === row.engines.length - 1}
                                                    onClick={() => set(moveEngine(row.engines, j, 1))}
                                                >
                                                    ›
                                                </ChipButton>
                                                <ChipButton
                                                    label="remove"
                                                    disabled={disabled || row.engines.length === 1}
                                                    onClick={() => set(toggleEngine(row.engines, engine))}
                                                >
                                                    ×
                                                </ChipButton>
                                            </span>
                                        ))}
                                        {missingEngines(row.engines).map((engine) => (
                                            <button
                                                key={engine}
                                                type="button"
                                                disabled={disabled}
                                                onClick={() => set(toggleEngine(row.engines, engine))}
                                                className="border border-dashed border-gray-600 text-gray-400 hover:text-white rounded-sm px-1 py-0.5 disabled:opacity-50"
                                                title={`Add ${ENGINE_LABELS[engine]} at the end`}
                                            >
                                                + {ENGINE_LABELS[engine]}
                                            </button>
                                        ))}
                                    </div>
                                </td>
                                <td className="py-1 text-right">
                                    {(!keepWildcard || row.qualifier !== WILDCARD) && (
                                        <button
                                            type="button"
                                            disabled={disabled}
                                            onClick={() => onChange(removeRow(rows, row.op, row.qualifier, keepWildcard))}
                                            className="text-gray-400 hover:text-red-300 px-1 disabled:opacity-50"
                                            title="Remove this row"
                                        >
                                            Remove
                                        </button>
                                    )}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <select
                    className="bg-gray-800 border border-gray-700 rounded-sm px-1 py-0.5 text-white"
                    value={newOp}
                    disabled={disabled}
                    onChange={(e) => setNewOp(e.target.value as ExecOperation)}
                >
                    {EXEC_OPERATIONS.map((op) => (
                        <option key={op} value={op}>
                            {OPERATION_LABELS[op]}
                        </option>
                    ))}
                </select>
                <input
                    className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-0.5 text-white font-mono w-40"
                    placeholder={keepWildcard ? QUALIFIER_HINTS[newOp] : `${QUALIFIER_HINTS[newOp]} or *`}
                    value={newQualifier}
                    disabled={disabled}
                    onChange={(e) => setNewQualifier(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter") {
                            e.preventDefault();
                            add();
                        }
                    }}
                />
                <button
                    type="button"
                    disabled={disabled || !newQualifier.trim()}
                    onClick={add}
                    className="bg-gray-700 hover:bg-gray-600 px-2 py-0.5 rounded-sm disabled:opacity-50"
                >
                    Add row
                </button>
                {addError && <span className="text-red-300">{addError}</span>}
            </div>
        </div>
    );
};

const ChipButton: React.FC<{label: string; disabled: boolean; onClick: () => void; children: React.ReactNode}> = ({
    label,
    disabled,
    onClick,
    children,
}) => (
    <button
        type="button"
        aria-label={label}
        title={label}
        disabled={disabled}
        onClick={onClick}
        className="px-0.5 text-gray-300 hover:text-white disabled:opacity-30"
    >
        {children}
    </button>
);

export default ExecutionPolicyTable;
