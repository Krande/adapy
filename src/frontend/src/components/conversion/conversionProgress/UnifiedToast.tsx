// The one toast in-progress conversions, operations and the scene load share.

import React from "react";
import type { LoadTask } from "@/state/loadQueueStore";
import type { ConversionJob } from "@/state/conversionStore";
import { ConversionRows, LoadRow, basename, type ToastJob } from "./jobRows";

// Unified activity toast — ONE box for a model's whole lifecycle
// (queue → conversion → upload → load). The scene-load row sits at the top
// as the furthest-along stage; conversion-in-progress rows follow, then the
// load queue and any load errors. Folding the load in here (rather than a
// second LoadQueueToast box) makes the "Loading" counter replace the
// conversion counter in place instead of popping a separate toast.
export const UnifiedToast: React.FC<{
    conversionJobs: ToastJob[];
    onClearJob: (storeKey: string) => void;
    loadName: string | null;
    loadJob?: ConversionJob;
    loadQueued: LoadTask[];
    loadErrors: Array<{name: string; message: string}>;
    onRemoveQueued: (name: string) => void;
    onClearLoadError: (name: string) => void;
}> = ({
    conversionJobs, onClearJob, loadName, loadJob,
    loadQueued, loadErrors, onRemoveQueued, onClearLoadError,
}) => {
    if (
        !loadName && conversionJobs.length === 0 &&
        loadQueued.length === 0 && loadErrors.length === 0
    ) {
        return null;
    }
    return (
        <div className="bg-gray-800 text-gray-100 rounded-sm shadow-lg px-3 py-2 text-xs border border-gray-700 space-y-1">
            {loadName && <LoadRow name={loadName} job={loadJob}/>}
            {conversionJobs.length > 0 && (
                <div className={loadName ? "pt-1 border-t border-gray-700/60" : ""}>
                    <ConversionRows jobs={conversionJobs} onClearJob={onClearJob}/>
                </div>
            )}
            {loadQueued.length > 0 && (
                <div className="pt-1 border-t border-gray-700/60">
                    <div className="text-[11px] text-gray-400 mb-0.5">
                        Queued ({loadQueued.length})
                    </div>
                    <ul className="space-y-0.5 max-h-40 overflow-auto">
                        {loadQueued.map((t) => (
                            <li key={t.name} className="flex items-center gap-2 min-w-0">
                                <span className="truncate flex-1 min-w-0" title={t.name}>
                                    {basename(t.name)}
                                </span>
                                <button
                                    type="button"
                                    onClick={() => onRemoveQueued(t.name)}
                                    className="shrink-0 px-1 rounded-sm text-gray-400 hover:text-red-300 hover:bg-gray-700 cursor-pointer"
                                    title="Remove from load queue"
                                    aria-label={`Remove ${basename(t.name)} from load queue`}
                                >
                                    ×
                                </button>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
            {loadErrors.length > 1 && (
                <div className="flex items-center pt-1 border-t border-gray-700/60 text-[11px] text-gray-400">
                    <span className="flex-1">{loadErrors.length} failed loads</span>
                    <button
                        type="button"
                        onClick={() => loadErrors.forEach((e) => onClearLoadError(e.name))}
                        className="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-0.5 rounded-sm"
                        title={`Dismiss all ${loadErrors.length} load errors`}
                    >
                        Clear all
                    </button>
                </div>
            )}
            <div className={loadErrors.length > 1 ? "max-h-48 overflow-auto space-y-1" : "space-y-1"}>
            {loadErrors.map((e) => (
                <div
                    key={e.name}
                    className="flex items-start justify-between gap-2 pt-1 border-t border-gray-700/60"
                >
                    <div className="min-w-0">
                        <div className="truncate" title={e.name}>{basename(e.name)}</div>
                        <div className="text-[11px] text-red-400 break-all">{e.message}</div>
                    </div>
                    <button
                        type="button"
                        onClick={() => onClearLoadError(e.name)}
                        className="shrink-0 px-1 rounded-sm text-gray-400 hover:text-white hover:bg-gray-700 cursor-pointer"
                        title="Dismiss"
                        aria-label={`Dismiss error for ${basename(e.name)}`}
                    >
                        ×
                    </button>
                </div>
            ))}
            </div>
        </div>
    );
};
