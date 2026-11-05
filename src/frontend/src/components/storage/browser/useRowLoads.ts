import {useState} from "react";
import {useConversionStore} from "@/state/conversionStore";
import {useLoadQueueStore} from "@/state/loadQueueStore";
import {viewerApi} from "@/services/viewerApi";
import {confirmDialog} from "@/plugins/dialogs";
import type {RowLoadProgress} from "./FileRow";
import {loadJobKeys, resolveRowLoadState} from "./rowLoadProgress";

// Each row's scene load, told on the row (the panel's `rowLoadProgress`): its state, and
// the Cancel and Dismiss that act on it.
export function useRowLoads(p: {
    enabled: boolean;
    scopeKey: string;
    viewingName: string | null;
    queuedLoadNames: ReadonlySet<string>;
}) {
    const {enabled, scopeKey, viewingName, queuedLoadNames} = p;
    const jobs = useConversionStore((s) => s.jobs);
    const loadErrors = useLoadQueueStore((s) => s.errors);
    // Loads cancelled from their row: shown as cancelled until dismissed or retried.
    const [cancelled, setCancelled] = useState<ReadonlySet<string>>(() => new Set());
    const mark = (name: string, on: boolean) =>
        setCancelled((prev) => {
            if (prev.has(name) === on) return prev;
            const next = new Set(prev);
            if (on) next.add(name);
            else next.delete(name);
            return next;
        });

    const stateFor = (name: string) =>
        resolveRowLoadState(name, {
            jobs,
            loadCurrentName: viewingName,
            queuedNames: queuedLoadNames,
            loadErrors,
            cancelledNames: cancelled,
        });

    const cancel = async (name: string) => {
        const target = stateFor(name).cancel;
        if (!target) return;
        if (!await confirmDialog({
            title: "Cancel loading?",
            body: [`"${name}" stops loading.`, "The conversion running for it is stopped too."],
            confirmLabel: "Cancel loading",
            cancelLabel: "Keep loading",
            tone: "danger",
        })) return;
        // It may have finished while the dialog was open.
        const job = useConversionStore.getState().jobs[target.storeKey];
        if (!job || (job.status !== "queued" && job.status !== "running")) return;
        const jobId = job.jobId || target.jobId;
        if (jobId && scopeKey) {
            try {
                await viewerApi.cancelMyJob(scopeKey, jobId);
            } catch (err) {
                // Stopped locally regardless: a row stuck on a job nobody watches is worse.
                console.warn(`[storage] cancel ${jobId} failed`, err);
            }
        }
        mark(name, true);
        // Removing the entry stops the client side: a streaming bake aborts, and a
        // server conversion's poll fails the load on its next tick.
        useConversionStore.getState().clearJob(target.storeKey);
    };

    const dismiss = (name: string) => {
        const {dismissKeys} = stateFor(name);
        useLoadQueueStore.getState().clearError(name);
        const conv = useConversionStore.getState();
        for (const k of dismissKeys) conv.clearJob(k);
        mark(name, false);
    };

    /** A retry starts clean: the last attempt's finished entries would sit under it. */
    const beforeEnqueue = (name: string) => {
        const conv = useConversionStore.getState();
        for (const k of loadJobKeys(name)) {
            const j = conv.jobs[k];
            if (j && (j.status === "error" || j.status === "cancelled")) conv.clearJob(k);
        }
        mark(name, false);
    };

    const progressFor = (name: string): RowLoadProgress | undefined =>
        enabled ? {state: stateFor(name), onCancel: () => void cancel(name), onDismiss: () => dismiss(name)} : undefined;

    /** The row menu's Cancel, while this row's load can be stopped. */
    const cancelFor = (name: string) =>
        enabled && viewingName === name && stateFor(name).cancel ? () => void cancel(name) : undefined;

    /** The row menu's Dismiss, while this row shows an error or a cancellation. */
    const dismissFor = (name: string) => {
        if (!enabled || viewingName === name) return undefined;
        const phase = stateFor(name).phase;
        return phase === "error" || phase === "cancelled" ? () => dismiss(name) : undefined;
    };

    return {progressFor, cancelFor, dismissFor, beforeEnqueue};
}
