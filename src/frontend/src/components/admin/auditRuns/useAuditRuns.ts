import {useCallback, useEffect, useState} from "react";
import {viewerApi, AuditRun, AuditRunJob} from "@/services/viewerApi";
import {POLL_INTERVAL_MS} from "./gridMetrics";

// Recent runs in reverse-chronological order plus the selected run's detail,
// polled every 5 s while any visible run is still running so in-flight runs
// visibly advance their counters without manual refresh.
export function useAuditRuns() {
    const [runs, setRuns] = useState<AuditRun[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [selectedRun, setSelectedRun] = useState<AuditRun | null>(null);
    const [selectedJobs, setSelectedJobs] = useState<AuditRunJob[]>([]);
    const [listError, setListError] = useState<string | null>(null);
    const [detailError, setDetailError] = useState<string | null>(null);

    const loadRuns = useCallback(async () => {
        try {
            const r = await viewerApi.adminAuditRunsList({limit: 30});
            setRuns(r.runs);
            setListError(null);
        } catch (e) {
            setListError((e as Error).message || "failed to load audit runs");
        }
    }, []);

    const loadDetail = useCallback(async (runId: string) => {
        try {
            const r = await viewerApi.adminAuditRunGet(runId);
            setSelectedRun(r.run);
            setSelectedJobs(r.jobs);
            setDetailError(null);
        } catch (e) {
            setDetailError((e as Error).message || "failed to load run");
        }
    }, []);

    useEffect(() => { void loadRuns(); }, [loadRuns]);

    // Poll while any visible run is still running — saves the user
    // hitting refresh while the dispatcher's BackgroundTask fills in
    // ``total`` and workers stream their outcomes.
    useEffect(() => {
        const anyRunning = runs.some((r) => r.status === "running")
            || (selectedRun?.status === "running");
        if (!anyRunning) return;
        const id = window.setInterval(() => {
            void loadRuns();
            if (selectedId) void loadDetail(selectedId);
        }, POLL_INTERVAL_MS);
        return () => window.clearInterval(id);
    }, [runs, selectedRun, selectedId, loadRuns, loadDetail]);

    /** Reload both the list and the selected run's detail. */
    const reloadAll = useCallback(() => {
        void loadRuns();
        if (selectedId) void loadDetail(selectedId);
    }, [loadRuns, loadDetail, selectedId]);

    return {
        runs,
        selectedId,
        setSelectedId,
        selectedRun,
        setSelectedRun,
        selectedJobs,
        listError,
        detailError,
        loadRuns,
        loadDetail,
        reloadAll,
    };
}
