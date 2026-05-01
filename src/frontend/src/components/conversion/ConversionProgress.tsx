import React, {useState} from "react";
import {useConversionStore} from "@/state/conversionStore";

const STATUS_LABEL: Record<string, string> = {
    queued: "Queued",
    running: "Converting",
    done: "Ready",
    error: "Failed",
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
