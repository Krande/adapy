import React from "react";
import {RefreshButton} from "../AdminTabShell";

// Header row of the admin storage tab: scope + count, overrides toggle,
// selection actions, clear-all-derived, compression sweep, refresh, and the
// published-assets toggle.
const StorageToolbar: React.FC<{
    scopeName: string;
    fileCount: number;
    overrideOpen: boolean;
    activeOverrides: number;
    onToggleOverrides: () => void;
    selectedCount: number;
    busyKey: string | null;
    onMoveSelected: () => void;
    onClearSelection: () => void;
    totalDerived: number;
    onClearAllDerived: () => void;
    compressionBusy: boolean;
    compressionMsg: string | null;
    onCompress: () => void;
    loading: boolean;
    onRefresh: () => void;
    showHidden: boolean;
    onShowHidden: (v: boolean) => void;
    hiddenCount: number;
}> = ({
    scopeName,
    fileCount,
    overrideOpen,
    activeOverrides,
    onToggleOverrides,
    selectedCount,
    busyKey,
    onMoveSelected,
    onClearSelection,
    totalDerived,
    onClearAllDerived,
    compressionBusy,
    compressionMsg,
    onCompress,
    loading,
    onRefresh,
    showHidden,
    onShowHidden,
    hiddenCount,
}) => (
    <>
        <span className="text-gray-400">
            Scope: <span className="text-white">{scopeName}</span>
        </span>
        <span className="text-gray-500">·</span>
        <span className="text-gray-400">{fileCount} source{fileCount === 1 ? "" : "s"}</span>
        <button
            className="ml-2 bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded-sm text-[11px]"
            onClick={onToggleOverrides}
            title="Per-conversion overrides applied to all Convert clicks on this tab"
        >
            Overrides{activeOverrides ? ` (${activeOverrides})` : ""} {overrideOpen ? "▾" : "▸"}
        </button>
        {selectedCount > 0 && (
            <>
                <span className="ml-2 text-gray-300">
                    {selectedCount} selected
                </span>
                <button
                    type="button"
                    className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded-sm text-[11px] disabled:opacity-50"
                    onClick={onMoveSelected}
                    disabled={busyKey === "__bulk_move__"}
                    title="Rename selected sources under a folder prefix"
                >
                    {busyKey === "__bulk_move__" ? "Moving…" : "Move to folder…"}
                </button>
                <button
                    type="button"
                    className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded-sm text-[11px]"
                    onClick={onClearSelection}
                    title="Clear selection"
                >
                    Clear
                </button>
            </>
        )}
        {totalDerived > 0 && (
            <button
                type="button"
                className="bg-red-900/70 hover:bg-red-800 px-2 py-1 rounded-sm text-[11px] text-gray-100 disabled:opacity-50"
                onClick={onClearAllDerived}
                disabled={busyKey === "__clear_all_derived__"}
                title={
                    "Delete every cached derived product (GLB / FEA blob set) " +
                    "in this scope. Sources are preserved."
                }
            >
                {busyKey === "__clear_all_derived__"
                    ? `Clearing… (${totalDerived})`
                    : `Clear all derived (${totalDerived})`}
            </button>
        )}
        <button
            type="button"
            className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded-sm text-[11px] disabled:opacity-50"
            onClick={onCompress}
            disabled={compressionBusy}
            title={
                "Sweep this scope for source files (.ifc / .step / .sif / etc.) " +
                "uploaded uncompressed (via the direct presigned-PUT path for " +
                "files >200 MB) and rewrite each with Content-Encoding: gzip. " +
                "Runs in the background; safe to leave the panel."
            }
        >
            {compressionMsg ?? "Compress uncompressed sources"}
        </button>
        {/* Inline spinner so feedback is visual on mobile, not just a text
            swap. The label stays "Refresh" so the user can re-tap to
            abort+retry without wondering whether the button is disabled. */}
        <RefreshButton
            onClick={onRefresh}
            loading={loading}
            title={loading ? "Refreshing — tap again to retry" : "Refresh storage list"}
        />
        {/* Published assets are storage rather than files, so they are
            off by default even here. The count is on the label so the
            toggle says what it would reveal — an unlabelled switch
            hiding four thousand blobs is not a choice, it is a guess. */}
        <label
            className="flex items-center gap-1.5 text-xs text-gray-300 cursor-pointer select-none"
            title={
                "Show machine-published dataset blobs under assets/. They are always " +
                "returned by the API — plugins index their published hierarchy from " +
                "exactly this listing — and only hidden from these browsers by default."
            }
        >
            <input
                type="checkbox"
                checked={showHidden}
                onChange={(e) => onShowHidden(e.target.checked)}
                className="accent-blue-600"
                disabled={hiddenCount === 0}
            />
            <span className={hiddenCount === 0 ? "text-gray-600" : undefined}>
                Show published assets
                {hiddenCount > 0 ? ` (${hiddenCount.toLocaleString()})` : ""}
            </span>
        </label>
    </>
);

export default StorageToolbar;
