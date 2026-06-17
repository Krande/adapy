// Client-side executor for an audit-run sweep whose worker_pool is "wasm".
//
// The server creates the run and computes its cell total but enqueues
// nothing (no NATS); the browser drives every cell here through the same
// pyodide pipelines the manual conversions use, writing each cell's audit
// row via audit/local with the run's audit_run_id so the run counters
// advance and the files×targets grid fills exactly like a worker sweep.
//
// Cells the WASM engine can't do (non-GLB targets, or sources like .odb)
// are recorded as ``skipped`` so the run still closes. Runs sequentially
// (one pyodide worker) and resumes on reload — cells already terminal for
// this run come back with done=true and are skipped.

import {viewerApi, ScopeUrl} from "@/services/viewerApi";
import {wasmSupportsConversion} from "@/services/conversion/wasmSupport";
import {convertViaPyodideAndUpload} from "@/services/conversion/pyodidePipeline";

const WASM_SWEEP_IMAGE_TAG = "wasm:sweep";

export interface WasmSweepProgress {
    total: number;
    completed: number;
    current: string | null;
}

/**
 * Run all not-yet-done cells of a WASM audit run in the browser. Resolves
 * when every cell has a terminal audit row. ``onProgress`` fires before
 * each cell and once at the end. Never throws for a single failed cell —
 * the conversion pipeline records an ``error`` audit row (tagged with the
 * run) and the sweep moves on.
 */
export async function runWasmAuditSweep(
    scope: ScopeUrl,
    runId: string,
    onProgress?: (p: WasmSweepProgress) => void,
): Promise<void> {
    const {cells} = await viewerApi.adminAuditRunCells(runId);
    const total = cells.length;
    const pending = cells.filter((c) => !c.done);
    let completed = total - pending.length;
    onProgress?.({total, completed, current: null});

    for (const cell of pending) {
        onProgress?.({total, completed, current: `${cell.source_key} → ${cell.target_format}`});
        try {
            if (wasmSupportsConversion(cell.source_key, cell.target_format)) {
                // convertViaPyodideAndUpload records its own audit row
                // (running→done/error) tagged with audit_run_id. Routes the
                // cell's target through the same matrix the viewer uses.
                await convertViaPyodideAndUpload(scope, cell.source_key, cell.target_format, {
                    auditRunId: runId,
                });
            } else {
                // Not convertible in-browser — close the cell as skipped so
                // the run's counters reach total.
                const jobId = await viewerApi.auditLocalCreate(scope, {
                    key: cell.source_key,
                    target_format: cell.target_format,
                    audit_run_id: runId,
                    image_tag: WASM_SWEEP_IMAGE_TAG,
                });
                await viewerApi.auditLocalUpdate(scope, jobId, {status: "skipped"});
            }
        } catch {
            // A GLB cell that threw already has an error audit row from the
            // pipeline; keep sweeping the rest.
        }
        completed += 1;
        onProgress?.({total, completed, current: null});
    }
}
