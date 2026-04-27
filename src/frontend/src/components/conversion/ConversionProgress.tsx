import React from "react";
import {useConversionStore} from "@/state/conversionStore";

const STATUS_LABEL: Record<string, string> = {
    queued: "Queued",
    running: "Converting",
    done: "Ready",
    error: "Failed",
};

const ConversionProgress = () => {
    const jobs = useConversionStore((s) => s.jobs);
    const clearJob = useConversionStore((s) => s.clearJob);

    const visible = Object.values(jobs).filter(
        (j) => j.status === "queued" || j.status === "running" || j.status === "error"
    );

    if (visible.length === 0) {
        return null;
    }

    return (
        <div className="absolute bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
            {visible.map((job) => {
                const pct = Math.round((job.progress || 0) * 100);
                const isError = job.status === "error";
                return (
                    <div
                        key={job.sourceKey}
                        className="bg-gray-800 text-gray-100 rounded shadow-lg px-3 py-2 text-xs border border-gray-700"
                    >
                        <div className="flex justify-between items-center mb-1">
                            <span className="truncate">{job.sourceKey}</span>
                            <span className="ml-2 text-gray-400">
                                {STATUS_LABEL[job.status] || job.status}
                                {!isError && ` ${pct}%`}
                            </span>
                        </div>
                        {!isError && (
                            <div className="h-1 bg-gray-700 rounded overflow-hidden">
                                <div
                                    className="h-full bg-blue-500 transition-all"
                                    style={{width: `${Math.max(pct, 4)}%`}}
                                />
                            </div>
                        )}
                        {isError && (
                            <div className="flex justify-between items-start gap-2">
                                <div className="text-red-400 break-all">{job.error}</div>
                                <button
                                    className="shrink-0 text-gray-400 hover:text-gray-200"
                                    onClick={() => clearJob(job.sourceKey)}
                                >
                                    ×
                                </button>
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
};

export default ConversionProgress;
