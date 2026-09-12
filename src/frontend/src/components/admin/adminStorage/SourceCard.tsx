import React from "react";
import {TargetFormat} from "@/services/viewerApi";
import {runtime} from "@/runtime/config";
import {localDate} from "@/utils/time";
import {RowKebabMenu} from "@/components/common/RowKebabMenu";
import type {RowProps} from "./SourceRow";
import {formatBytes, suggestedName} from "./format";

// Mobile card for one source file.

interface CardProps extends RowProps {
    expanded: boolean;
    onToggleExpand: () => void;
}

const SourceCard: React.FC<CardProps> = ({
    file,
    depth,
    busyKey,
    expanded,
    selected,
    onToggleSelected,
    onToggleExpand,
    onConvert,
    onDownload,
    onDelete,
    onDeleteDerived,
    onDeleteAllDerived,
    onMoveToFolder,
}) => {
    const downloadable = file.available_targets.filter((t) => t !== "glb");
    const busyConverting =
        busyKey?.startsWith(`${file.key}::`) &&
        !busyKey.endsWith("::delete") &&
        busyKey !== `${file.key}::delete-all-derived`;
    const busyDeleting = busyKey === `${file.key}::delete`;
    const busyDeletingAllDerived = busyKey === `${file.key}::delete-all-derived`;
    const busyMoving = busyKey === `${file.key}::move`;
    const displayName = file.key.includes("/") ? file.key.split("/").pop() : file.key;
    return (
        <li className="px-3 py-3 text-xs" style={{paddingLeft: `${0.75 + depth * 1.0}rem`}}>
            <div className="flex items-start gap-2">
                <input
                    type="checkbox"
                    checked={selected}
                    onChange={onToggleSelected}
                    onClick={(e) => e.stopPropagation()}
                    className="mt-1 shrink-0"
                    aria-label={`Select ${file.key}`}
                    disabled={file.orphan === true}
                    title={file.orphan ? "Orphans can't be moved" : "Select for batch operations"}
                />
                <button
                    type="button"
                    className="flex-1 min-w-0 text-left"
                    onClick={onToggleExpand}
                >
                    <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-sm truncate" title={file.key}>
                            {file.orphan && (
                                <span className="text-[10px] uppercase text-yellow-400 mr-1">orphan</span>
                            )}
                            {displayName}
                        </span>
                        <span className="text-[11px] text-gray-400 shrink-0">{formatBytes(file.size)}</span>
                    </div>
                    <div className="text-gray-400 mt-0.5">
                        {file.format}
                        {file.last_modified ? ` · ${localDate(file.last_modified)}` : ""}
                        {file.derived.length > 0 ? ` · ${file.derived.length} derived` : ""}
                    </div>
                </button>
                <div className="shrink-0">
                    <RowKebabMenu
                        ariaLabel={`Organize ${file.key}`}
                        disabled={file.orphan === true || busyMoving}
                        buttonClassName="h-9 w-9"
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
            </div>
            {expanded && (
                <div className="mt-2 space-y-2">
                    {file.derived.length > 0 && (
                        <div className="flex flex-wrap gap-1 items-center">
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
                                            title="Delete cached derived blob"
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
                    )}
                    <div className="flex flex-wrap gap-1">
                        {!file.orphan && (
                            <button
                                className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded-sm text-xs"
                                onClick={() => onDownload(file.key, file.key)}
                            >
                                Download
                            </button>
                        )}
                        {!file.orphan && runtime.convertEnabled() && downloadable.length > 0 && (
                            <select
                                disabled={busyConverting || false}
                                className="bg-gray-700 hover:bg-gray-600 text-xs rounded-sm px-2 py-1 disabled:opacity-50"
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
                            className="bg-red-800 hover:bg-red-700 px-2 py-1 rounded-sm text-xs disabled:opacity-50"
                            onClick={() => onDelete(file.key, file.key)}
                            disabled={busyDeleting}
                        >
                            {busyDeleting ? "…" : "Delete"}
                        </button>
                    </div>
                </div>
            )}
        </li>
    );
};

export default SourceCard;
