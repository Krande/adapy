// A storage-compression sweep's progress, as its own toast.

import React from "react";
import { DismissButton } from "./buttons";

// Compression-sweep toast row — same visual language as the
// conversion toast below; rendered in the same bottom-right slot so
// progress UX stays consistent across "convert one file" and
// "compress all files in a scope" workflows. Stale rows (server
// flagged ``orphaned`` because the pod restarted) get a one-line
// note + a dismiss button so the user can clear them.
export const CompressionToast: React.FC<{
    scopeLabel: string;
    state: import("@/services/viewerApi").CompressionSweepState;
    onDismiss: () => void;
}> = ({scopeLabel, state, onDismiss}) => {
    const pct = state.total > 0
        ? Math.round((state.processed / state.total) * 100)
        : 0;
    const finished = state.completed_at !== null;
    const failed = !!state.error || state.errors.length > 0;
    const orphaned = state.orphaned;

    let label: string;
    if (orphaned) {
        label = "Stalled";
    } else if (state.error) {
        label = "Failed";
    } else if (finished) {
        const savedMb = Math.max(
            0, (state.bytes_before - state.bytes_after) / 1024 / 1024,
        );
        label = state.compressed > 0
            ? `Compressed ${state.compressed} (saved ${savedMb.toFixed(0)} MB)`
            : state.already_gzipped > 0
                ? `All ${state.already_gzipped} already gzipped`
                : "Nothing to compress";
    } else {
        label = `Compressing ${state.processed} / ${state.total}`;
    }

    const subtitle = state.current_key || `scope: ${scopeLabel}`;

    return (
        <div className="bg-gray-800 text-gray-100 rounded-sm shadow-lg px-3 py-2 text-xs border border-gray-700">
            <div className="flex justify-between items-center mb-1 gap-2">
                <span className="truncate flex-1" title={subtitle}>
                    {subtitle}
                </span>
                <span className={`ml-2 ${failed || orphaned ? "text-red-400" : "text-gray-400"}`}>
                    {label}
                    {!finished && !orphaned && state.total > 0 && ` ${pct}%`}
                </span>
                {(finished || failed || orphaned) && (
                    <div className="ml-1">
                        <DismissButton onClick={onDismiss}/>
                    </div>
                )}
            </div>
            {!finished && !orphaned && state.total > 0 && (
                <div className="h-1 bg-gray-700 rounded-sm overflow-hidden">
                    <div
                        className="h-full bg-blue-500 transition-all"
                        style={{width: `${Math.max(pct, 4)}%`}}
                    />
                </div>
            )}
            {orphaned && (
                <div className="text-[11px] text-gray-300 mt-1">
                    Viewer restarted mid-sweep. Re-run from Admin → Storage to resume.
                </div>
            )}
            {state.error && (
                <div className="text-[11px] text-red-400 mt-1 break-all">
                    {state.error}
                </div>
            )}
            {state.errors.length > 0 && !state.error && (
                <div className="text-[11px] text-red-400 mt-1">
                    {state.errors.length} file{state.errors.length === 1 ? "" : "s"} failed
                </div>
            )}
        </div>
    );
};
