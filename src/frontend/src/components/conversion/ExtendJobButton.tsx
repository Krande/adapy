import React, {useState} from "react";

import {viewerApi} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

/** What "Extend" offers: added to the running job's time limit. */
export const EXTEND_STEPS: {label: string; seconds: number}[] = [
    {label: "+15 min", seconds: 15 * 60},
    {label: "+30 min", seconds: 30 * 60},
    {label: "+1 h", seconds: 60 * 60},
];

/** Extend a running job's time limit -- for one seen near its limit with most of its work done.
 *  Says the job's total extension once one is made. `scope` is the URL scope to ask through (a
 *  sweep's own); the current scope when not given. Shared by the toast and the sweep table. */
const ExtendJobButton: React.FC<{jobId: string; label: string; scope?: string; className?: string}> = ({
    jobId,
    label,
    scope,
    className = "ml-2",
}) => {
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [total, setTotal] = useState<number | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const current = useScopeStore((s) => s.current);

    const extend = async (seconds: number) => {
        const via = scope ?? (current ? scopeUrlPart(current) : null);
        if (!via) return;
        setBusy(true);
        setErr(null);
        try {
            const got = await viewerApi.extendMyJob(via, jobId, seconds);
            if (got === null) setErr("not extended: the job is finished, or not yours");
            else setTotal(got);
            setOpen(false);
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    const btn = "bg-gray-700 hover:bg-gray-600 text-gray-100 rounded-sm px-1.5 py-0.5 disabled:opacity-50";
    return (
        <div className={`flex items-center gap-1 text-[11px] shrink-0 ${className}`}>
            {total !== null && !open && (
                <span className="text-gray-400" title="What this job's time limit has been extended by">
                    +{Math.round(total / 60)} min
                </span>
            )}
            {open ? (
                <>
                    {EXTEND_STEPS.map((s) => (
                        <button
                            key={s.seconds}
                            type="button"
                            disabled={busy}
                            className={btn}
                            onClick={() => void extend(s.seconds)}
                        >
                            {s.label}
                        </button>
                    ))}
                    <button type="button" disabled={busy} className={btn} onClick={() => setOpen(false)}>
                        No
                    </button>
                </>
            ) : (
                <button
                    type="button"
                    className={btn}
                    title={`Extend the time limit of ${label}`}
                    aria-label={`Extend the time limit of ${label}`}
                    onClick={() => setOpen(true)}
                >
                    Extend
                </button>
            )}
            {err && <span className="text-red-400 break-all">{err}</span>}
        </div>
    );
};

export default ExtendJobButton;
