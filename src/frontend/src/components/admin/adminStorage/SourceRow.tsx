import React from "react";
import {AdminFileEntry, TargetFormat} from "@/services/viewerApi";
import {runtime} from "@/runtime/config";
import {RowKebabMenu} from "@/components/common/RowKebabMenu";
import {STORAGE_COLUMNS} from "./useResizableColumns";
import {Td, fmtIsoLocal, formatBytes, suggestedName} from "./format";

// Desktop table row for one source file, plus its expandable derived-products
// row.

export interface RowProps {
    file: AdminFileEntry;
    /** Indent level for the Name column — 0 for top-level entries,
     *  +1 per folder nesting level. */
    depth: number;
    busyKey: string | null;
    selected: boolean;
    onToggleSelected: () => void;
    onConvert: (sourceKey: string, target: TargetFormat) => void;
    onDownload: (key: string, suggestedName: string) => void;
    onDelete: (key: string, label: string) => void;
    onDeleteDerived: (sourceKey: string, derivedKey: string, label: string) => void;
    onDeleteAllDerived: (file: AdminFileEntry) => void;
    onMoveToFolder: (key: string) => void;
}

const SourceRow: React.FC<RowProps & {scope: string; expanded: boolean; onToggleExpand: () => void}> = ({
    file,
    depth,
    busyKey,
    selected,
    onToggleSelected,
    onConvert,
    onDownload,
    onDelete,
    onDeleteDerived,
    onDeleteAllDerived,
    onMoveToFolder,
    expanded,
    onToggleExpand,
}) => {
    const downloadable = file.available_targets.filter((t) => t !== "glb");
    // Convert / per-blob-derived-delete keys both start with file.key:: ;
    // we need to also exclude the row-level "delete-all-derived" key from
    // the converting busy match, otherwise the Convert select shows a
    // spinner while a derived bulk-delete is in flight.
    const busyConverting =
        busyKey?.startsWith(`${file.key}::`) &&
        !busyKey.endsWith("::delete") &&
        busyKey !== `${file.key}::delete-all-derived`;
    const busyDeleting = busyKey === `${file.key}::delete`;
    const busyDeletingAllDerived = busyKey === `${file.key}::delete-all-derived`;
    const busyMoving = busyKey === `${file.key}::move`;
    const derivedCount = file.derived.length;
    return (
        <>
        <tr className="border-t border-gray-800 align-top">
            <Td>
                <input
                    type="checkbox"
                    checked={selected}
                    onChange={onToggleSelected}
                    aria-label={`Select ${file.key}`}
                    disabled={file.orphan === true}
                    title={file.orphan ? "Orphans can't be moved" : "Select for batch operations"}
                />
            </Td>
            <Td title={file.key}>
                <div className="flex items-center gap-1" style={{paddingLeft: `${depth * 1.25}rem`}}>
                    {/* Chevron toggles the derived-products overview below. Disabled (kept for
                        alignment) when the source has no derived products. */}
                    <button
                        type="button"
                        onClick={onToggleExpand}
                        disabled={derivedCount === 0}
                        aria-expanded={expanded}
                        title={derivedCount === 0 ? "No derived products" : "Show derived products"}
                        className={"shrink-0 w-4 text-gray-400 hover:text-gray-100 disabled:opacity-30 " +
                            (derivedCount === 0 ? "cursor-default" : "cursor-pointer")}
                    >
                        <span className={"inline-block transition-transform " + (expanded ? "rotate-90" : "")}>▸</span>
                    </button>
                    {file.orphan && (
                        <span className="text-[10px] uppercase text-yellow-400" title="Source missing">
                            orphan
                        </span>
                    )}
                    {/* Filename only; the folder prefix is already shown
                        in the parent folder rows. Falls back to the full
                        key when the source is at the root (no slash). */}
                    <span className="truncate">{file.key.includes("/") ? file.key.split("/").pop() : file.key}</span>
                    {derivedCount > 0 && (
                        <span className="shrink-0 text-[10px] text-gray-400 bg-gray-700/60 rounded-sm px-1">
                            {derivedCount}
                        </span>
                    )}
                </div>
            </Td>
            <Td>{file.format}</Td>
            <Td>{formatBytes(file.size)}</Td>
            <Td title={file.last_modified || ""}>
                {fmtIsoLocal(file.last_modified)}
            </Td>
            <Td>
                <div className="flex flex-wrap gap-1 justify-end">
                    {!file.orphan && (
                        <button
                            className="bg-gray-700 hover:bg-gray-600 px-2 py-0.5 rounded-sm text-xs"
                            onClick={() => onDownload(file.key, file.key)}
                        >
                            DL
                        </button>
                    )}
                    {!file.orphan && runtime.convertEnabled() && downloadable.length > 0 && (
                        <select
                            disabled={busyConverting || false}
                            className="bg-gray-700 hover:bg-gray-600 text-xs rounded-sm px-1 py-0.5 disabled:opacity-50"
                            value=""
                            onChange={(e) => {
                                const t = e.target.value as TargetFormat | "";
                                e.target.value = "";
                                if (t) onConvert(file.key, t);
                            }}
                        >
                            <option value="">{busyConverting ? "…" : "Convert ▾"}</option>
                            {downloadable.map((t) => (
                                <option key={t} value={t}>{t.toUpperCase()}</option>
                            ))}
                        </select>
                    )}
                    <button
                        className="bg-red-800 hover:bg-red-700 px-2 py-0.5 rounded-sm text-xs disabled:opacity-50"
                        onClick={() => onDelete(file.key, file.key)}
                        disabled={busyDeleting}
                        title="Delete source + all derived"
                    >
                        {busyDeleting ? "…" : "Delete"}
                    </button>
                    <RowKebabMenu
                        ariaLabel={`Organize ${file.key}`}
                        disabled={file.orphan === true || busyMoving}
                        items={[
                            {
                                key: "move-to-folder",
                                label: busyMoving ? "Moving…" : "Move to folder…",
                                disabled: busyMoving,
                                onClick: () => onMoveToFolder(file.key),
                            },
                        ]}
                    />
                </div>
            </Td>
        </tr>
        {expanded && derivedCount > 0 && (
            <tr className="bg-gray-900/40">
                <td/>
                <td colSpan={STORAGE_COLUMNS.length - 1} className="px-3 pb-2 pl-9">
                    <div className="flex flex-wrap gap-1 items-center">
                        <span className="text-[11px] text-gray-400 mr-1">Derived products:</span>
                        {file.derived.map((d) => {
                            const busyDerived = busyKey === `${d.key}::delete`;
                            return (
                                <span key={d.key} className="inline-flex rounded-sm overflow-hidden border border-gray-700">
                                    <button
                                        className="bg-gray-800 hover:bg-gray-700 px-2 py-0.5 text-[11px]"
                                        onClick={() => onDownload(d.key, suggestedName(file.key, d.format))}
                                        title={`${d.key} (${formatBytes(d.size)})`}
                                    >
                                        {d.format.toUpperCase()} ↓
                                    </button>
                                    <button
                                        className="bg-red-900/70 hover:bg-red-800 px-1.5 text-[11px] text-gray-100 disabled:opacity-50"
                                        onClick={() => onDeleteDerived(file.key, d.key, `${file.key} → ${d.format}`)}
                                        disabled={busyDerived}
                                        title="Delete cached derived blob (next Convert will regenerate it)"
                                    >
                                        {busyDerived ? "…" : "×"}
                                    </button>
                                </span>
                            );
                        })}
                        {file.derived.length > 1 && (
                            <button
                                className="bg-red-900/70 hover:bg-red-800 px-2 py-0.5 rounded-sm text-[11px] text-gray-100 disabled:opacity-50"
                                onClick={() => onDeleteAllDerived(file)}
                                disabled={busyDeletingAllDerived}
                                title="Delete every cached derived blob for this source"
                            >
                                {busyDeletingAllDerived
                                    ? `Deleting… (${file.derived.length})`
                                    : `Delete all (${file.derived.length})`}
                            </button>
                        )}
                    </div>
                </td>
            </tr>
        )}
        </>
    );
};

export default SourceRow;
