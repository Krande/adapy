// What the admin audit badge shows: the running sweeps, polled from the audit-active
// endpoint -- every 2 s while one runs, every 15 s otherwise, and not at all without a
// database (where no audit can run) or for a non-admin (the endpoint 403s).

import { useEffect, useState } from "react";

import { runtime } from "@/runtime/config";
import { ApiError } from "@/services/api/client";
import { viewerApi } from "@/services/viewerApi";

export type AuditActiveSummary = {
    running_runs: number;
    pending_cells: number;
    current_cell: {
        key: string | null;
        target_format: string | null;
        status: string | null;
        started_at: string | null;
        elapsed_ms: number | null;
    } | null;
};

/** The latest summary of running audit sweeps, or null before the first answer. */
export function useAuditActivity(isAdmin: boolean): AuditActiveSummary | null {
    const [summary, setSummary] = useState<AuditActiveSummary | null>(null);
    // No database (desktop / local mode; config.js says so, else a 503), so no audit
    // can run on it -- stop asking rather than log a 503 every 15s.
    const [unavailable, setUnavailable] = useState(() => !runtime.hasDatabase());

    useEffect(() => {
        if (!isAdmin || unavailable) return;
        let cancelled = false;
        const poll = async () => {
            try {
                const r = await viewerApi.adminAuditActive();
                if (!cancelled) setSummary(r);
            } catch (err) {
                // Network blip or auth dropped — silently retry on
                // the next tick. The badge stays at its last
                // known state in the meantime.
                if (!cancelled && err instanceof ApiError && err.status === 503) setUnavailable(true);
            }
        };
        void poll();
        // 2s while an audit is running so the "now: …" line keeps
        // up with actual cell turnover (cells flip every couple of
        // seconds on a force-rebuild). When nothing's running we
        // revert to the cheap 15s cadence — ``summary`` re-mounts
        // the effect implicitly because ``running_runs`` only flips
        // on the next poll, so this is a one-knob compromise.
        const interval = summary && summary.running_runs > 0 ? 2_000 : 15_000;
        const id = window.setInterval(poll, interval);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, [isAdmin, unavailable, summary && summary.running_runs > 0]);
    return summary;
}
