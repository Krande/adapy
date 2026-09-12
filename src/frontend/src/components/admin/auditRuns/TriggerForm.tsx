import React, {useCallback, useEffect, useState} from "react";
import {viewerApi, Corpus} from "@/services/viewerApi";
import {runWasmAuditSweep, WasmSweepProgress} from "@/services/audit/wasmSweep";
import {ImagePool, describeImagePool, groupWorkersByImage} from "../auditPools";

// "Run audit" form — pick a scope (a corpus for release-gate sweeps, or an
// ad-hoc scope) and an optional worker pool, fire one POST to
// /admin/audit/runs.

// Synthetic worker-pool value routing a run to the in-browser WASM engine.
const WASM_POOL = "wasm";

const TriggerForm: React.FC<{onCreated: () => void}> = ({onCreated}) => {
    const [scope, setScope] = useState("shared");
    const [workerPool, setWorkerPool] = useState("");
    const [note, setNote] = useState("");
    const [forceRebuild, setForceRebuild] = useState(false);
    // When on, the run auto-fires a follow-up validate_only parity pass once it
    // finishes (replaces the old standalone "Run validation" button).
    const [autoValidate, setAutoValidate] = useState(false);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    // In-browser sweep progress (WASM pool only); null when idle.
    const [sweep, setSweep] = useState<WasmSweepProgress | null>(null);
    const [sweepErr, setSweepErr] = useState<string | null>(null);
    const isWasmPool = workerPool.trim().toLowerCase() === WASM_POOL;
    // Online workers grouped by IMAGE (see auditPools). The picker used to
    // list capability tags, which chose a fleet back when each capability was
    // its own image; with one combined image, six tags resolve to the same
    // pods. If a pod isn't registered yet its image won't show up here either,
    // which is the honest signal.
    const [imagePools, setImagePools] = useState<ImagePool[]>([]);
    // Available corpora (M3). Audit sweeps against a curated corpus
    // are the release-gate flow; sweeping shared/user scopes is
    // mostly for ad-hoc debugging.
    const [corpora, setCorpora] = useState<Corpus[]>([]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const r = await viewerApi.adminListWorkers();
                if (cancelled) return;
                setImagePools(groupWorkersByImage(r.workers));
            } catch {
                // No-op: the picker just falls back to "any" + a free
                // hint. Failure to list workers shouldn't break audit
                // dispatch — the operator can still type a tag.
            }
        })();
        (async () => {
            try {
                const r = await viewerApi.adminCorporaList();
                if (cancelled) return;
                setCorpora(r.corpora);
            } catch {
                // Non-fatal: scope picker still has shared / user:me.
            }
        })();
        return () => { cancelled = true; };
    }, []);

    const createRun = useCallback(async () => {
        setBusy(true);
        setErr(null);
        setSweepErr(null);
        try {
            const run = await viewerApi.adminAuditRunCreate({
                scope,
                worker_pool: workerPool.trim() || null,
                note: note.trim() || null,
                force_rebuild: forceRebuild,
                auto_validate: autoValidate,
            });
            setNote("");
            onCreated();
            // A WASM run is created server-side but dispatches nothing — the
            // browser drives its cells here. Fire-and-forget: the runs list
            // polls and reflects progress from the audit rows the sweep
            // writes; we also surface a local progress line.
            if (isWasmPool) {
                setSweep({total: 0, completed: 0, current: null});
                void runWasmAuditSweep(scope, run.id, (p) => setSweep(p))
                    .then(() => onCreated())
                    .catch((e) => setSweepErr((e as Error).message || "wasm sweep failed"))
                    .finally(() => setSweep(null));
            }
        } catch (e) {
            setErr((e as Error).message || "audit run create failed");
        } finally {
            setBusy(false);
        }
    }, [scope, workerPool, note, forceRebuild, autoValidate, onCreated, isWasmPool]);

    const onSubmit = useCallback((e: React.FormEvent) => {
        e.preventDefault();
        void createRun();
    }, [createRun]);

    return (
        <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2 px-3 py-2 border-b border-gray-800 bg-gray-900/40">
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Scope</span>
                <select
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-64"
                    title="Pick a corpus for release-gate sweeps, or a non-corpus scope for ad-hoc debugging."
                >
                    {corpora.length > 0 && (
                        <optgroup label="Corpora (release-gate)">
                            {corpora.map((c) => (
                                <option key={c.slug} value={`corpus:${c.slug}`}>
                                    corpus:{c.slug}
                                </option>
                            ))}
                        </optgroup>
                    )}
                    <optgroup label="Ad-hoc">
                        <option value="shared">shared</option>
                        <option value="user:me">user:me</option>
                    </optgroup>
                </select>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Worker pool</span>
                <select
                    value={workerPool}
                    onChange={(e) => setWorkerPool(e.target.value)}
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-40"
                    title={
                        imagePools.length === 0
                            ? "No online workers found; pool restriction won't take effect"
                            : "Which fleet runs the sweep. Images are what an operator " +
                              "reasons about — routing is still by capability subject, so " +
                              "an image is only bindable while it is the sole provider of " +
                              "the capability it serves."
                    }
                >
                    <option value="">any pool</option>
                    <option value={WASM_POOL}>WASM (in-browser)</option>
                    {imagePools.length > 0 && (
                        <optgroup label="Worker images">
                            {imagePools.map((p) => (
                                <option
                                    key={p.imageTag || "(untagged)"}
                                    value={p.routeCapability}
                                    title={`serves: ${p.capabilities.join(", ") || "nothing"}`}
                                >
                                    {describeImagePool(p)}
                                    {p.enforceable ? "" : " (shared — cannot bind)"}
                                </option>
                            ))}
                        </optgroup>
                    )}
                </select>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1 flex-1 min-w-[200px]">
                <span>Note <span className="text-gray-500">(optional)</span></span>
                <input
                    type="text"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="release v0.8 dry run"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100"
                />
            </label>
            <label
                className="text-xs text-gray-300 flex items-center gap-1 h-[30px] mt-auto select-none"
                title={
                    "Skip the dispatcher's cached-blob short-circuit so every cell " +
                    "actually re-converts. Use for perf measurements; a second run " +
                    "against the same scope otherwise short-circuits ~80% of cells " +
                    "against prior outputs."
                }
            >
                <input
                    type="checkbox"
                    checked={forceRebuild}
                    onChange={(e) => setForceRebuild(e.target.checked)}
                    className="accent-blue-600"
                />
                <span>Force rebuild</span>
            </label>
            <label
                className="text-xs text-gray-300 flex items-center gap-1 h-[30px] mt-auto select-none"
                title={
                    isWasmPool
                        ? "Auto-validate runs on the worker pool only; ignored for in-browser (WASM) sweeps."
                        : "After this run finishes, automatically start a validation pass " +
                          "(cross-format visual-parity per source) for the same scope."
                }
            >
                <input
                    type="checkbox"
                    checked={autoValidate}
                    disabled={isWasmPool}
                    onChange={(e) => setAutoValidate(e.target.checked)}
                    className="accent-teal-600 disabled:opacity-40"
                />
                <span className={isWasmPool ? "opacity-40" : undefined}>Validate after</span>
            </label>
            <button
                type="submit"
                disabled={busy}
                className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm px-3 py-1 rounded-sm h-[30px]"
            >
                {busy ? "Starting…" : "Run audit"}
            </button>
            {err && (
                <div className="w-full text-xs text-red-400" role="alert">{err}</div>
            )}
            {isWasmPool && (
                <div className="w-full text-xs text-amber-300/90">
                    In-browser sweep: runs in this tab via the WASM engine — keep it open until it finishes.
                    Reopening resumes (completed cells are skipped); non-WASM cells (e.g. <code>.odb</code>,
                    non-GLB targets) are recorded as skipped.
                </div>
            )}
            {sweep && (
                <div className="w-full text-xs text-gray-300" role="status">
                    Sweeping {sweep.completed}/{sweep.total}
                    {sweep.current ? <> — <span className="font-mono text-gray-400">{sweep.current}</span></> : null}
                </div>
            )}
            {sweepErr && (
                <div className="w-full text-xs text-red-400" role="alert">sweep: {sweepErr}</div>
            )}
        </form>
    );
};

export default TriggerForm;
