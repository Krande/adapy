import React, {useCallback, useEffect, useState} from "react";
import {viewerApi} from "@/services/viewerApi";
import {useAuditToastStore} from "@/state/auditToastStore";
import {view_in_3d} from "@/utils/scene/handlers/view_in_3d";
import {METRIC_LABELS, MetricKey, RuntimeMode, cellDerivedKey} from "./gridMetrics";
import RunGrid from "./RunGrid";
import TriggerForm from "./TriggerForm";
import RunList from "./RunList";
import {CancelRunButton, DeleteRunButton, IssueBotStatus, ReDispatchButton, ValidateRunButton} from "./RunActions";
import {CellDetailsModal, CellHistoryModal} from "./CellModals";
import {useAuditRuns} from "./useAuditRuns";

// Admin tab — kick off regression sweeps across the converter matrix
// and drill into per-cell results. Layer 1 of the audit panel from
// the admin audit-panel design notes:
//
//   * "Run audit" form — pick a scope (M3 will add a corpus picker)
//     and an optional worker pool, fire one POST to /admin/audit/runs.
//   * History list — recent runs in reverse-chronological order,
//     polled every 5 s so in-flight runs visibly advance their
//     counters without manual refresh.
//   * Per-run drill-in — files × targets grid with cell coloring on
//     pass/fail/cached and a metric switcher that recolors the same
//     grid by peak_rss / elapsed_s / mem_per_input_mb / write_bytes.

const AuditRunsTab: React.FC = () => {
    const {
        runs, selectedId, setSelectedId, selectedRun, setSelectedRun, selectedJobs,
        listError, detailError, loadRuns, loadDetail, reloadAll,
    } = useAuditRuns();
    const [metric, setMetric] = useState<MetricKey>("status");
    // Runtime shown in the overview: sum-of-cell-times vs active wall clock.
    // Persisted so the operator's choice sticks across visits.
    const [runtimeMode, setRuntimeMode] = useState<RuntimeMode>(
        () => (localStorage.getItem("auditRuntimeMode") === "wall" ? "wall" : "cells"),
    );
    useEffect(() => { localStorage.setItem("auditRuntimeMode", runtimeMode); }, [runtimeMode]);
    // Ambient "audit sweep in progress" toast (shown over the viewer) — operators can hide it here.
    const toastHidden = useAuditToastStore((s) => s.hidden);
    const toggleToast = useAuditToastStore((s) => s.toggle);
    // New-run form: collapsible on mobile (always visible on md+). Auto-collapses
    // when a run is opened so the run detail owns the small screen.
    const [formOpen, setFormOpen] = useState(true);
    // Cell whose cross-run history modal is open (from the grid context menu).
    const [historyCell, setHistoryCell] = useState<{key: string; target: string} | null>(null);
    // Cell whose full-detail modal is open (status/metrics/error).
    const [detailsCell, setDetailsCell] = useState<{file: string; target: string} | null>(null);

    const onSelectRun = useCallback((runId: string) => {
        setSelectedId(runId);
        // Collapse the new-run form on mobile so the selected run's grid gets
        // the viewport (no-op visually on md+, where the form is always shown).
        setFormOpen(false);
        void loadDetail(runId);
    }, [loadDetail, setSelectedId]);

    // ``md:`` breakpoint switches from stacked (mobile) to side-by-side
    // (desktop) — Tailwind's ``md`` is 768 px. Below md the history
    // list collapses out of view once a run is selected so the grid
    // gets full screen width; the "← back" button in the per-run
    // header restores the list.
    const showHistory = !selectedId;  // only matters on mobile

    return (
        <div className="flex flex-col h-full">
            {/* Mobile-only collapse header for the new-run form. On md+ the form
                is always shown (this button is hidden), matching desktop where
                screen space isn't scarce. */}
            <button
                type="button"
                onClick={() => setFormOpen((o) => !o)}
                className="md:hidden flex items-center justify-between w-full px-3 py-2 border-b border-gray-800 bg-gray-900/40 text-xs text-gray-200"
                aria-expanded={formOpen}
            >
                <span>New audit run</span>
                <span className="text-gray-400">{formOpen ? "▾ hide" : "▸ show"}</span>
            </button>
            <div className={(formOpen ? "block" : "hidden") + " md:block"}>
                <TriggerForm onCreated={loadRuns}/>
            </div>

            <div className="flex-1 min-h-0 flex flex-col md:flex-row overflow-hidden">
                <RunList
                    runs={runs}
                    selectedId={selectedId}
                    onSelect={onSelectRun}
                    visible={showHistory}
                    listError={listError}
                    runtimeMode={runtimeMode}
                    onRuntimeMode={setRuntimeMode}
                    toastHidden={toastHidden}
                    onToggleToast={toggleToast}
                />

                {/* Per-run grid. Hidden on mobile when no run is
                    selected so the history list owns the viewport. */}
                <div className={
                    "flex-1 min-h-0 flex-col overflow-hidden " +
                    (showHistory ? "hidden md:flex" : "flex")
                }>
                    {!selectedRun && (
                        <div className="hidden md:block text-xs text-gray-500 italic px-4 py-6">
                            Pick a run from the list to see its file × target grid.
                        </div>
                    )}
                    {selectedRun && (
                        <>
                            <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-3 flex-wrap">
                                <div className="flex items-center gap-2 min-w-0">
                                    {/* Mobile-only back link. On desktop
                                        the history list is always
                                        visible so this would be
                                        redundant. */}
                                    <button
                                        type="button"
                                        onClick={() => setSelectedId(null)}
                                        className="md:hidden text-sm text-blue-400 hover:text-blue-300 shrink-0"
                                        title="Back to run list"
                                    >
                                        ← list
                                    </button>
                                    <div className="text-xs text-gray-300 min-w-0">
                                        <div className="font-mono truncate">
                                            {selectedRun.seq != null && (
                                                <span className="text-gray-500 mr-1">#{selectedRun.seq}</span>
                                            )}
                                            {selectedRun.scope}
                                        </div>
                                        <div className="text-gray-500">
                                            ok {selectedRun.ok} · failed {selectedRun.failed} ·
                                            skipped {selectedRun.skipped} · total {selectedRun.total}
                                        </div>
                                        <IssueBotStatus
                                            run={selectedRun}
                                            onChanged={() => selectedId && loadDetail(selectedId)}
                                        />
                                    </div>
                                </div>
                                <div className="flex items-center gap-2 shrink-0">
                                    {selectedRun.status === "running" ? (
                                        <CancelRunButton run={selectedRun} onCancelled={reloadAll}/>
                                    ) : (
                                        <>
                                            <ValidateRunButton run={selectedRun} onValidated={reloadAll}/>
                                            <ReDispatchButton
                                                run={selectedRun}
                                                onDispatched={() => { void loadRuns(); }}
                                            />
                                            <DeleteRunButton
                                                run={selectedRun}
                                                onDeleted={() => {
                                                    setSelectedId(null);
                                                    setSelectedRun(null);
                                                    void loadRuns();
                                                }}
                                            />
                                        </>
                                    )}
                                    <label className="text-xs text-gray-300 flex items-center gap-2">
                                        <span className="hidden sm:inline">Color cells by:</span>
                                        <span className="sm:hidden">Metric:</span>
                                        <select
                                            value={metric}
                                            onChange={(e) => setMetric(e.target.value as MetricKey)}
                                            className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-xs text-gray-100"
                                        >
                                            {(Object.keys(METRIC_LABELS) as MetricKey[]).map((k) => (
                                                <option key={k} value={k}>{METRIC_LABELS[k]}</option>
                                            ))}
                                        </select>
                                    </label>
                                </div>
                            </div>
                            {detailError && (
                                <div className="text-xs text-red-400 px-3 py-2">{detailError}</div>
                            )}
                            <div className="flex-1 min-h-0 overflow-hidden">
                                <RunGrid
                                    jobs={selectedJobs}
                                    metric={metric}
                                    onCellHistory={(file, target) => setHistoryCell({key: file, target})}
                                    onCellDetails={(file, target) => setDetailsCell({file, target})}
                                    onCellOpen={(file, target) => {
                                        // Load the cell's cached product into the
                                        // underlying scene, from the RUN's scope
                                        // (may differ from the browsed one).
                                        void view_in_3d(file, cellDerivedKey(file, target), selectedRun.scope);
                                    }}
                                    onCellRerun={(file, target) => {
                                        // Re-run just this cell in place (force rebuild). Reopens
                                        // the run; the poller then streams the fresh result in.
                                        void (async () => {
                                            try {
                                                await viewerApi.adminAuditRunRerunCell(selectedRun.id, file, target);
                                                await loadDetail(selectedRun.id);
                                            } catch (e) {
                                                window.alert(`Rerun failed: ${(e as Error).message}`);
                                            }
                                        })();
                                    }}
                                />
                            </div>
                        </>
                    )}
                </div>
            </div>
            {historyCell && (
                <CellHistoryModal cell={historyCell} onClose={() => setHistoryCell(null)}/>
            )}
            {detailsCell && selectedRun && (
                <CellDetailsModal
                    run={selectedRun}
                    file={detailsCell.file}
                    target={detailsCell.target}
                    job={selectedJobs.find(
                        (j) => j.key === detailsCell.file && j.target_format === detailsCell.target,
                    )}
                    onClose={() => setDetailsCell(null)}
                />
            )}
        </div>
    );
};

export default AuditRunsTab;
