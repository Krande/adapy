// Failed jobs in the progress toast: one row per error, with its traceback and a copy button.

import React, { useState } from "react";
import { DismissButton, InfoButton } from "./buttons";
import { RunActions, STATUS_LABEL, type ToastJob } from "./jobRows";

const ErrorRow: React.FC<{
    sourceKey: string;
    message: string;
    onClear: () => void;
}> = ({sourceKey, message, onClear}) => {
    const [copied, setCopied] = useState(false);

    const onCopy = async () => {
        const payload = `${sourceKey}\n${message}`;
        try {
            await navigator.clipboard.writeText(payload);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — user can still select-and-copy */
        }
    };

    return (
        <div className="flex flex-col gap-1">
            <div className="flex justify-between items-start gap-2">
                <pre className="text-red-400 break-all whitespace-pre-wrap font-mono text-[11px] leading-snug max-h-64 overflow-auto m-0">
                    {message}
                </pre>
                <div className="flex flex-col gap-1 shrink-0">
                    <DismissButton onClick={onClear}/>
                    <InfoButton/>
                </div>
            </div>
            <div className="flex justify-end">
                <button
                    type="button"
                    onClick={onCopy}
                    className="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-0.5 rounded-sm text-[11px]"
                    title="Copy traceback to clipboard"
                >
                    {copied ? "Copied" : "Copy"}
                </button>
            </div>
        </div>
    );
};

// Every failed job in ONE toast, one error at a time, newest first.
//
// One toast per failure was fine for one failure. A bulk action that fails for many members -- a
// set load, a batch request -- stacked a toast per error up the screen, each to be closed by hand.
// Here they flip with ‹ › and the count says how many; Dismiss clears the one shown, Clear all
// clears every one. Each keeps its own traceback and Copy button: the error that matters is
// usually one of many that look alike.
export const ErrorStack: React.FC<{
    jobs: ToastJob[];
    onClear: (storeKey: string) => void;
}> = ({jobs, onClear}) => {
    const [index, setIndex] = useState(0);
    if (jobs.length === 0) return null;
    const sorted = [...jobs].sort((a, b) => b.startedAt - a.startedAt);
    // Clamped, not reset: clearing one keeps the reader at the same place in the list.
    const at = Math.min(index, sorted.length - 1);
    const job = sorted[at];
    const many = sorted.length > 1;
    const arrow = "h-6 w-6 grid place-items-center rounded-sm text-gray-300 hover:text-white hover:bg-gray-700 disabled:opacity-30";
    return (
        <div
            role="alert"
            className="bg-gray-800 text-gray-100 rounded-sm shadow-lg px-3 py-2 text-xs border border-gray-700"
        >
            <div className="flex justify-between items-center gap-2 mb-1">
                <span className="truncate flex-1" title={job.sourceKey}>{job.sourceKey}</span>
                <span className="text-red-400 shrink-0">{STATUS_LABEL[job.status] || job.status}</span>
            </div>
            {many && (
                <div className="flex items-center gap-1 mb-1">
                    <button
                        type="button"
                        className={arrow}
                        aria-label="Previous error"
                        disabled={at === 0}
                        onClick={() => setIndex(at - 1)}
                    >
                        ‹
                    </button>
                    <span className="tabular-nums text-gray-300">
                        {at + 1} / {sorted.length}
                    </span>
                    <button
                        type="button"
                        className={arrow}
                        aria-label="Next error"
                        disabled={at === sorted.length - 1}
                        onClick={() => setIndex(at + 1)}
                    >
                        ›
                    </button>
                    <button
                        type="button"
                        className="ml-auto bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-0.5 rounded-sm text-[11px]"
                        title={`Dismiss all ${sorted.length} errors`}
                        onClick={() => sorted.forEach((j) => onClear(j.storeKey))}
                    >
                        Clear all
                    </button>
                </div>
            )}
            <ErrorRow
                key={job.storeKey}
                sourceKey={job.sourceKey}
                message={job.error || "(no error message)"}
                onClear={() => onClear(job.storeKey)}
            />
            {/* A finished sweep with failed collections: re-queue them from here. */}
            {job.storeKey.startsWith("sweep:") && !job.storeKey.includes("#") && <RunActions storeKey={job.storeKey}/>}
        </div>
    );
};
