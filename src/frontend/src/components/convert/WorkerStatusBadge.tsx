import React from "react";
import {runtime} from "@/runtime/config";

// Compact status pill: "N formats" pulled from the runtime config.
// Counts every source format in the live conversion matrix (CONVERSION_MATRIX,
// merged from every registered worker's advertised conversions) — not
// EXTRA_SOURCE_EXTS, which is only the handful of "extra"/streaming FEA-result
// extensions and badly under-counts (5 vs the full ~18). A non-empty matrix
// also implies at least one worker is registered; dev / desktop builds with no
// workers render a muted "no workers" pill instead so the page doesn't look
// broken.

const WorkerStatusBadge: React.FC = () => {
    const exts = Array.from(new Set(runtime.conversionMatrix().map((e) => e.from))).sort();
    const hasWorkers = exts.length > 0;

    return (
        <div
            className={
                "inline-flex items-center gap-2 px-3 py-1 rounded-sm text-xs " +
                (hasWorkers
                    ? "bg-pass-subtle border border-pass text-pass"
                    : "bg-surface-2 border border-edge text-content")
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
                    (hasWorkers ? "bg-pass" : "bg-surface-3")
                }
            />
            {hasWorkers
                ? `${exts.length} format${exts.length === 1 ? "" : "s"} online`
                : "no workers"}
        </div>
    );
};

export default WorkerStatusBadge;
