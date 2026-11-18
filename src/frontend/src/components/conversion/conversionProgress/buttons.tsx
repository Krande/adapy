// The progress toast's small buttons: dismiss, info, and cancel (with its confirmation).

import React, { useState } from "react";
import { useMeStore } from "@/state/meStore";
import { useScopeStore, scopeUrlPart } from "@/state/scopeStore";
import { viewerApi } from "@/services/viewerApi";

// Shared dismiss button. Hit area is 28×28 (Apple HIG min is 44 but
// the toast is dense; 28 still beats the 24 we shipped with and stays
// visually compact). ``pointer-events-auto`` defensively counters any
// ancestor that disables pointer events (the toast container is OK,
// but the surrounding viewer chrome shifts under us so being explicit
// avoids surprise). ``onClick`` stops propagation so a future "click
// on background dismisses" overlay won't swallow the click and so the
// click can't reach the 3D canvas's OrbitControls underneath if the
// stacking ever changes.
export const DismissButton: React.FC<{
    onClick: () => void;
    label?: string;
    disabled?: boolean;
}> = ({onClick, label = "Dismiss", disabled = false}) => (
    <button
        type="button"
        onClick={(e) => {
            e.stopPropagation();
            onClick();
        }}
        onMouseDown={(e) => e.stopPropagation()}
        disabled={disabled}
        aria-label={label}
        title={label}
        className={
            "shrink-0 inline-flex items-center justify-center w-7 h-7 rounded-sm " +
            "border border-gray-600 bg-gray-700/60 text-gray-200 cursor-pointer " +
            "pointer-events-auto hover:bg-gray-600 hover:border-gray-400 hover:text-white " +
            "disabled:opacity-50 disabled:cursor-not-allowed"
        }
    >
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <path
                d="M4 4 L12 12 M12 4 L4 12"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                fill="none"
            />
        </svg>
    </button>
);

// (i) Info button — admin-only entry point that opens the Admin panel
// on the Audit Log tab so the operator can dig into the worker-side
// traceback for the failed job. Non-admins don't see it (they have no
// route to that data). Same hit-area + propagation guards as
// DismissButton.
export const InfoButton: React.FC<{title?: string}> = ({
    title = "Open audit log for traceback",
}) => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    if (!isAdmin) return null;
    return (
        <a
            href="/admin#audit"
            target="_blank"
            rel="noopener"
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
            aria-label={title}
            title={title}
            className={
                "shrink-0 inline-flex items-center justify-center w-7 h-7 rounded-sm " +
                "border border-blue-500/60 bg-blue-700/40 text-blue-200 cursor-pointer " +
                "pointer-events-auto hover:bg-blue-600 hover:border-blue-300 hover:text-white text-xs font-semibold no-underline"
            }
        >
            i
        </a>
    );
};

export const CancelButton: React.FC<{
    sourceKey: string;
    jobId: string;
    status: "queued" | "running";
    onCancelled: () => void;
    /** An operation: Kill stops it and every job under it, and says so. */
    operation?: boolean;
}> = ({sourceKey, jobId, status, onCancelled, operation}) => {
    const [confirming, setConfirming] = useState(false);
    const [busy, setBusy] = useState(false);
    const current = useScopeStore((s) => s.current);

    const verb = status === "queued" && !operation ? "Delete" : "Kill";
    const question = operation
        ? "Kill this and every job under it?"
        : status === "queued"
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
            <div className="ml-2">
                <DismissButton
                    onClick={() => setConfirming(true)}
                    label={`${verb} ${sourceKey}`}
                />
            </div>
        );
    }
    return (
        <div className="flex items-center gap-1 ml-2 text-[11px]">
            <span className="text-gray-300">{question}</span>
            <button
                disabled={busy}
                className="bg-red-700 hover:bg-red-600 text-white rounded-sm px-1.5 py-0.5 disabled:opacity-50"
                onClick={doCancel}
            >
                {busy ? "…" : verb}
            </button>
            <button
                disabled={busy}
                className="bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-sm px-1.5 py-0.5 disabled:opacity-50"
                onClick={() => setConfirming(false)}
            >
                No
            </button>
        </div>
    );
};
