import React from "react";
import {useLoadQueueStore} from "@/state/loadQueueStore";
import {useConversionStore} from "@/state/conversionStore";

// Scene-load progress toast — lives in the same bottom-right slot as
// the conversion toast (mounted from ConversionProgress) so all
// long-running feedback shares one visual language. Shows the model
// currently loading (with the conversion job's progress/stage when
// one is driving the load, else an indeterminate bar), the queued
// models (each removable), and per-model load errors.

const basename = (key: string) => key.split("/").pop() ?? key;

const LoadQueueToast: React.FC = () => {
    const current = useLoadQueueStore((s) => s.current);
    const queued = useLoadQueueStore((s) => s.queued);
    const errors = useLoadQueueStore((s) => s.errors);
    const removeQueued = useLoadQueueStore((s) => s.removeQueued);
    const clearError = useLoadQueueStore((s) => s.clearError);
    const jobs = useConversionStore((s) => s.jobs);

    if (!current && queued.length === 0 && errors.length === 0) return null;

    // The load may be waiting on a conversion/bake job — surface that
    // job's progress + stage. Keys are `${sourceKey}::<target>`.
    const job = current
        ? Object.entries(jobs).find(([k]) => k.startsWith(current.name + "::"))?.[1]
        : undefined;
    const pct = job ? Math.round((job.progress || 0) * 100) : null;

    return (
        <div className="bg-gray-800 text-gray-100 rounded-sm shadow-lg px-3 py-2 text-xs border border-gray-700 space-y-1">
            {current && (
                <div className="space-y-1 min-w-0">
                    <div className="flex justify-between items-center gap-2 min-w-0">
                        <span className="truncate min-w-0 flex-1" title={current.name}>
                            {basename(current.name)}
                        </span>
                        <span className="ml-2 text-gray-400 shrink-0">
                            Loading{pct !== null ? ` ${pct}%` : "…"}
                        </span>
                    </div>
                    {job?.stage && (
                        <div className="text-[11px] text-gray-400 truncate" title={job.stage}>
                            {job.stage}
                        </div>
                    )}
                    <div className="h-1 bg-gray-700 rounded-sm overflow-hidden">
                        {pct !== null ? (
                            <div
                                className="h-full bg-blue-500 transition-all"
                                style={{width: `${Math.max(pct, 4)}%`}}
                            />
                        ) : (
                            <div className="h-full w-1/3 bg-blue-500 animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                        )}
                    </div>
                </div>
            )}
            {queued.length > 0 && (
                <div className={current ? "pt-1 border-t border-gray-700/60" : ""}>
                    <div className="text-[11px] text-gray-400 mb-0.5">
                        Queued ({queued.length})
                    </div>
                    <ul className="space-y-0.5 max-h-40 overflow-auto">
                        {queued.map((t) => (
                            <li key={t.name} className="flex items-center gap-2 min-w-0">
                                <span className="truncate flex-1 min-w-0" title={t.name}>
                                    {basename(t.name)}
                                </span>
                                <button
                                    type="button"
                                    onClick={() => removeQueued(t.name)}
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
            {errors.map((e) => (
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
                        onClick={() => clearError(e.name)}
                        className="shrink-0 px-1 rounded-sm text-gray-400 hover:text-white hover:bg-gray-700 cursor-pointer"
                        title="Dismiss"
                        aria-label={`Dismiss error for ${basename(e.name)}`}
                    >
                        ×
                    </button>
                </div>
            ))}
        </div>
    );
};

export default LoadQueueToast;
