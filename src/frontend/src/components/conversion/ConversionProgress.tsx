import React, {useState} from "react";
import {useConversionStore} from "@/state/conversionStore";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {viewerApi} from "@/services/viewerApi";

const STATUS_LABEL: Record<string, string> = {
    queued: "Queued",
    running: "Converting",
    done: "Ready",
    error: "Failed",
};

const CancelButton: React.FC<{
    sourceKey: string;
    jobId: string;
    status: "queued" | "running";
    onCancelled: () => void;
}> = ({sourceKey, jobId, status, onCancelled}) => {
    const [confirming, setConfirming] = useState(false);
    const [busy, setBusy] = useState(false);
    const current = useScopeStore((s) => s.current);

    const verb = status === "queued" ? "Delete" : "Kill";
    const question =
        status === "queued"
            ? "Delete this queued job?"
            : "Kill this running conversion?";

    const doCancel = async () => {
        setBusy(true);
        try {
            if (jobId && current) {
                const scope = scopeUrlPart(current);
                if (scope) {
                    try {
                        await viewerApi.cancelMyJob(scope, jobId);
                    } catch (err) {
                        // Network failure or 5xx — log but still drop
                        // the row from the local store so the user
                        // isn't stuck staring at it.
                        // eslint-disable-next-line no-console
                        console.warn(`[cancel] ${jobId}`, err);
                    }
                }
            }
            // Always clear locally. For stuck-from-crash entries
            // (jobId === ""), the backend row may not exist at all;
            // this just removes the visual artefact.
            onCancelled();
        } finally {
            setBusy(false);
        }
    };

    if (!confirming) {
        return (
            <button
                className="shrink-0 text-gray-400 hover:text-gray-200 text-base leading-none ml-2"
                onClick={() => setConfirming(true)}
                aria-label={`${verb} ${sourceKey}`}
                title={`${verb} this conversion`}
            >
                ×
            </button>
        );
    }
    return (
        <div className="flex items-center gap-1 ml-2 text-[11px]">
            <span className="text-gray-300">{question}</span>
            <button
                disabled={busy}
                className="bg-red-700 hover:bg-red-600 text-white rounded px-1.5 py-0.5 disabled:opacity-50"
                onClick={doCancel}
            >
                {busy ? "…" : verb}
            </button>
            <button
                disabled={busy}
                className="bg-gray-700 hover:bg-gray-600 text-gray-200 rounded px-1.5 py-0.5 disabled:opacity-50"
                onClick={() => setConfirming(false)}
            >
                No
            </button>
        </div>
    );
};

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
                <button
                    className="shrink-0 text-gray-400 hover:text-gray-200"
                    onClick={onClear}
                    aria-label="Dismiss"
                    title="Dismiss"
                >
                    ×
                </button>
            </div>
            <div className="flex justify-end">
                <button
                    type="button"
                    onClick={onCopy}
                    className="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-0.5 rounded text-[11px]"
                    title="Copy traceback to clipboard"
                >
                    {copied ? "Copied" : "Copy"}
                </button>
            </div>
        </div>
    );
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
                const isCancellable = job.status === "queued" || job.status === "running";
                return (
                    <div
                        key={job.sourceKey}
                        className="bg-gray-800 text-gray-100 rounded shadow-lg px-3 py-2 text-xs border border-gray-700"
                    >
                        <div className="flex justify-between items-center mb-1">
                            <span className="truncate flex-1">{job.sourceKey}</span>
                            <span className="ml-2 text-gray-400">
                                {STATUS_LABEL[job.status] || job.status}
                                {!isError && ` ${pct}%`}
                            </span>
                            {isCancellable && (
                                <CancelButton
                                    sourceKey={job.sourceKey}
                                    jobId={job.jobId}
                                    status={job.status as "queued" | "running"}
                                    onCancelled={() => clearJob(job.sourceKey)}
                                />
                            )}
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
                            <ErrorRow
                                sourceKey={job.sourceKey}
                                message={job.error || "(no error message)"}
                                onClear={() => clearJob(job.sourceKey)}
                            />
                        )}
                    </div>
                );
            })}
        </div>
    );
};

export default ConversionProgress;
