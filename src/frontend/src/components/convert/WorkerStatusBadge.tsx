import React from "react";
import {runtime} from "@/runtime/config";

// Compact status pill: "N formats" pulled from the runtime config.
// The /api/config payload merges every live worker's source_exts into
// EXTRA_SOURCE_EXTS, so a non-empty list here also implies at least one
// worker is registered. Dev / desktop builds with no workers render a
// muted "no workers" pill instead so the page doesn't look broken.

const WorkerStatusBadge: React.FC = () => {
    const exts = runtime.extraSourceExts();
    const hasWorkers = exts.length > 0;

    return (
        <div
            className={
                "inline-flex items-center gap-2 px-3 py-1 rounded-sm text-xs " +
                (hasWorkers
                    ? "bg-emerald-700/30 border border-emerald-600/50 text-emerald-200"
                    : "bg-gray-700/40 border border-gray-600 text-gray-300")
            }
            title={
                hasWorkers
                    ? `Extensions advertised by live workers: ${exts.join(", ")}`
                    : "No conversion workers reported in to NATS yet"
            }
        >
            <span
                className={
                    "w-2 h-2 rounded-full " +
                    (hasWorkers ? "bg-emerald-400" : "bg-gray-500")
                }
            />
            {hasWorkers
                ? `${exts.length} format${exts.length === 1 ? "" : "s"} online`
                : "no workers"}
        </div>
    );
};

export default WorkerStatusBadge;
