import React, {useCallback, useEffect, useRef, useState} from "react";

import type {SweepItem, SweepRun} from "@/services/viewerApi";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {sweepSkipTarget, trackSweep, untrackSweep} from "@/services/sweepTracking";
import {useRunFocusStore} from "@/state/runFocusStore";
import {useConversionStore} from "@/state/conversionStore";

// Admin → Providers → a provider's sweeps: every collection, one job each, each published -- and
// what every phase of each took. See `routes/asset_sweeps.py`.
//
// A MEASUREMENT FIRST. The table shows each collection's phases side by side -- the wait for a
// worker, the provider's own login / export / stage, core's derive and write -- so the slow phase,
// and the slow collection, can be read off directly. The footer sums each column; nothing is
// averaged away.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));
const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const POLL_MS = 5000;

export function duration(ms: number | null | undefined): string {
    if (ms == null || !Number.isFinite(ms)) return "—";
    if (ms < 1000) return `${Math.round(ms)} ms`;
    const s = ms / 1000;
    if (s < 60) return `${s.toFixed(1)} s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${Math.round(s % 60)}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** The columns, each read off an item's timings. */
const COLUMNS: {
    key: string;
    title: string;
    hint: string;
    get: (i: SweepItem) => number | undefined;
    /** A short qualifier shown after the duration, e.g. how many databases a phase loaded. */
    note?: (i: SweepItem) => string | null;
}[] = [
    {key: "queued", title: "Queued", hint: "waiting for a worker to take the job", get: (i) => i.timings.queued_ms},
    {key: "login", title: "Login", hint: "the provider's login / session start", get: (i) => i.timings.provider?.login_ms},
    {
        key: "deferred",
        title: "Deferred",
        hint: "the provider loading databases its project defers (count after the time)",
        get: (i) => i.timings.provider?.deferred_ms,
        note: (i) => (i.timings.provider?.deferred_dbs != null ? `×${i.timings.provider.deferred_dbs}` : null),
    },
    {key: "export", title: "Export", hint: "the provider's export", get: (i) => i.timings.provider?.export_ms},
    {key: "stage", title: "Stage", hint: "the provider staging what it exported", get: (i) => i.timings.provider?.stage_ms},
    {key: "fetch", title: "Job", hint: "the provider's job, start to done, as the API saw it", get: (i) => i.timings.fetch_ms},
    {key: "derive", title: "Derive", hint: "core: the provider's reader deriving the publish plan", get: (i) => i.timings.publish?.derive_ms},
    {key: "write", title: "Write", hint: "core: writing the plan to the store", get: (i) => i.timings.publish?.write_ms},
    {key: "publish", title: "Publish", hint: "core's publish job, submit to done", get: (i) => i.timings.publish_ms},
    {key: "total", title: "Total", hint: "the whole collection", get: (i) => i.timings.total_ms},
];

const STATUS_TONE: Record<string, string> = {
    done: "text-emerald-300",
    unchanged: "text-emerald-300",
    nothing: "text-gray-400",
    error: "text-red-300",
    skipped: "text-gray-500",
    excluded: "text-gray-600",
    pending: "text-gray-500",
    fetching: "text-blue-300",
    publishing: "text-blue-300",
    running: "text-blue-300",
    cancelled: "text-gray-400",
    interrupted: "text-amber-300",
};

function countsText(run: SweepRun): string {
    const c = run.counts ?? {};
    const finished = (c.done ?? 0) + (c.unchanged ?? 0) + (c.nothing ?? 0) + (c.error ?? 0) + (c.skipped ?? 0);
    // Out of the collections that will run: ticked-off ones are not "left to do".
    const parts = [`${finished}/${run.total - (c.excluded ?? 0)}`];
    if (c.error) parts.push(`${c.error} failed`);
    if (c.excluded) parts.push(`${c.excluded} not run`);
    if (c.unchanged) parts.push(`${c.unchanged} unchanged`);
    return parts.join(" · ");
}

/** Whether an item's "run" box can still be changed: the sweep is running and has not reached it. */
export function canToggleRun(run: SweepRun, item: SweepItem): boolean {
    return run.status === "running" && (item.status === "pending" || item.status === "excluded");
}

const SweepItems: React.FC<{
    run: SweepRun;
    onSkip: (position: number) => void;
    onToggleRun: (position: number, on: boolean) => void;
}> = ({run, onSkip, onToggleRun}) => {
    // Only the item in flight can be skipped, and only once: a pending skip shows as "skipping…"
    // until the sweep carries it out (the item then ends `skipped`, "skipped by <user>").
    const skipAt = sweepSkipTarget(run);
    const items = run.items ?? [];
    const sums = COLUMNS.map((c) => items.reduce((acc, i) => acc + (c.get(i) ?? 0), 0));
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
                <thead className="text-left text-gray-500">
                    <tr>
                        <th
                            className="px-1.5 py-1 font-normal"
                            title="Run this collection when the sweep reaches it. Untick to leave it out; only collections not reached yet can change."
                        >
                            Run
                        </th>
                        <th className="px-1.5 py-1 font-normal">Collection</th>
                        <th className="px-1.5 py-1 font-normal">Status</th>
                        {COLUMNS.map((c) => (
                            <th key={c.key} className="px-1.5 py-1 font-normal text-right" title={c.hint}>
                                {c.title}
                            </th>
                        ))}
                        <th className="px-1.5 py-1 font-normal">Result</th>
                    </tr>
                </thead>
                <tbody>
                    {items.map((i) => (
                        <tr key={i.position} className="border-t border-gray-800 align-top">
                            <td className="px-1.5 py-1">
                                <input
                                    type="checkbox"
                                    aria-label={`Run ${i.collection}`}
                                    checked={i.status !== "excluded"}
                                    disabled={!canToggleRun(run, i)}
                                    onChange={(e) => onToggleRun(i.position, e.target.checked)}
                                />
                            </td>
                            <td className="px-1.5 py-1 font-mono">{i.collection}</td>
                            <td className={`px-1.5 py-1 ${STATUS_TONE[i.status] ?? ""}`}>
                                {i.status}
                                {i.timings.login?.mode && <span className="text-gray-500"> ({i.timings.login.mode})</span>}
                                {run.skip_position === i.position && <span className="text-amber-300"> skipping…</span>}
                                {skipAt === i.position && (
                                    <button
                                        type="button"
                                        className="ml-1 border border-gray-700 hover:bg-gray-800 px-1 rounded-sm text-gray-200"
                                        title="Give up on this collection and carry on with the next (the sweep keeps going)"
                                        onClick={() => onSkip(i.position)}
                                    >
                                        Skip
                                    </button>
                                )}
                            </td>
                            {COLUMNS.map((c) => (
                                <td key={c.key} className="px-1.5 py-1 text-right tabular-nums">
                                    {duration(c.get(i))}
                                    {c.note?.(i) && <span className="text-gray-500"> {c.note(i)}</span>}
                                </td>
                            ))}
                            <td className="px-1.5 py-1 max-w-[24rem]">
                                {i.stage || i.progress != null ? (
                                    <span className="text-blue-300 break-words">
                                        {i.job_status === "queued" ? "waiting for a worker" : (i.stage ?? i.job_status)}
                                        {i.progress != null && ` — ${Math.round(i.progress * 100)}%`}
                                        {i.started_at && ` (${duration(Date.now() - Date.parse(i.started_at))})`}
                                    </span>
                                ) : i.error ? (
                                    <span className="text-red-300 break-words">{i.error}</span>
                                ) : (
                                    <span className="text-gray-400 break-words">
                                        {i.revision ?? ""}
                                        {i.message ? `${i.revision ? " — " : ""}${i.message}` : ""}
                                    </span>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
                {items.length > 1 && (
                    <tfoot className="border-t border-gray-700 text-gray-300">
                        <tr>
                            <td className="px-1.5 py-1" colSpan={3}>
                                Sum
                            </td>
                            {sums.map((s, n) => (
                                <td key={COLUMNS[n].key} className="px-1.5 py-1 text-right tabular-nums">
                                    {s ? duration(s) : "—"}
                                </td>
                            ))}
                            <td />
                        </tr>
                    </tfoot>
                )}
            </table>
        </div>
    );
};

/** Watch this sweep in the global toast, which outlives this panel and a reload. Off by default:
 *  a sweep runs for hours, and a toast nobody asked for would sit over the viewer all that time. */
const ToastToggle: React.FC<{run: SweepRun}> = ({run}) => {
    const key = `sweep:${run.id}`;
    const on = useConversionStore((s) => !!s.jobs[key]);
    return (
        <label className="inline-flex items-center gap-1 text-gray-400" title="Show this sweep's progress in the global toast">
            <input
                type="checkbox"
                checked={on}
                onChange={(e) =>
                    e.target.checked
                        ? trackSweep(run.id, `Sweep ${run.provider} · ${run.job}${run.settings.sweep_target ? ` (${String(run.settings.sweep_target)})` : ""}`)
                        : untrackSweep(run.id)
                }
            />
            toast
        </label>
    );
};

const ProviderSweepRuns: React.FC<{provider: string; refreshKey: number}> = ({provider, refreshKey}) => {
    const [runs, setRuns] = useState<SweepRun[]>([]);
    const [open, setOpen] = useState<SweepRun | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const listed = await viewerApi.adminAssetSweeps(provider);
            setRuns(listed);
            setError(null);
        } catch (e) {
            setError(errText(e));
        }
    }, [provider]);

    const openRun = useCallback(async (id: string) => {
        try {
            setOpen(await viewerApi.adminAssetSweep(id));
        } catch (e) {
            setError(errText(e));
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load, refreshKey]);

    // "View run" from the toast: when the focused sweep is one of this provider's, open it and
    // scroll to it, then clear the focus (each provider's list looks; only the owner acts).
    const focus = useRunFocusStore((s) => s.focus);
    const rowRefs = useRef(new Map<string, HTMLLIElement>());
    useEffect(() => {
        if (focus?.kind !== "sweep" || !runs.some((r) => r.id === focus.runId)) return;
        const id = focus.runId;
        useRunFocusStore.getState().setFocus(null);
        void openRun(id).then(() => rowRefs.current.get(id)?.scrollIntoView({block: "center", behavior: "smooth"}));
    }, [focus, runs, openRun]);

    /** Skip the item at `position` (the one seen running). The run is re-read either way: a 409
     *  means the sweep moved on, and the fresh read shows to what. */
    const skip = useCallback(
        async (id: string, position: number) => {
            try {
                await viewerApi.adminSkipAssetSweepItem(id, position);
                setError(null);
            } catch (e) {
                setError(errText(e));
            }
            await load();
            if (open?.id === id) await openRun(id);
        },
        [load, openRun, open?.id],
    );

    /** Tick a collection the sweep has not reached on or off; the run is re-read either way, so a
     *  409 (the sweep got there first) shows what actually happened. */
    const toggleRun = useCallback(
        async (id: string, position: number, on: boolean) => {
            try {
                await viewerApi.adminSetAssetSweepItemRun(id, position, on);
                setError(null);
            } catch (e) {
                setError(errText(e));
            }
            await openRun(id);
            await load();
        },
        [load, openRun],
    );

    /** "Skip current" on the run row: the listing carries no items, so read the run for the item in
     *  flight first. */
    const skipCurrent = useCallback(
        async (id: string) => {
            try {
                const run = await viewerApi.adminAssetSweep(id);
                const position = sweepSkipTarget(run);
                if (position == null) {
                    setError(run.skip_position != null ? "A skip is already pending" : "Nothing is running in that sweep right now");
                    return;
                }
                await skip(id, position);
            } catch (e) {
                setError(errText(e));
            }
        },
        [skip],
    );

    // Poll while anything is running, and keep the open run's items current.
    const running = runs.some((r) => r.status === "running");
    const openId = open?.id ?? null;
    useEffect(() => {
        if (!running) return;
        const t = setInterval(() => {
            void load();
            if (openId) void openRun(openId);
        }, POLL_MS);
        return () => clearInterval(t);
    }, [running, openId, load, openRun]);

    if (!runs.length && !error) return null;
    return (
        <div className="space-y-1" data-testid="provider-sweep-runs">
            <div className="text-[11px] text-gray-400">Sweeps</div>
            {error && <div className="text-xs text-red-300">{error}</div>}
            <ul className="text-xs divide-y divide-gray-800 rounded-sm border border-gray-800">
                {runs.map((r) => (
                    <li
                        key={r.id}
                        className="px-2 py-1 space-y-1"
                        ref={(el) => {
                            if (el) rowRefs.current.set(r.id, el);
                            else rowRefs.current.delete(r.id);
                        }}
                    >
                        <div className="flex flex-wrap items-center gap-2">
                            <button
                                type="button"
                                className="text-blue-300 hover:text-blue-200"
                                onClick={() => (open?.id === r.id ? setOpen(null) : void openRun(r.id))}
                            >
                                {open?.id === r.id ? "▾" : "▸"} {fmt(r.created_at)}
                            </button>
                            <span className={STATUS_TONE[r.status] ?? ""}>{r.status}</span>
                            <span className="text-gray-400">{countsText(r)}</span>
                            <span className="text-gray-500">
                                {r.job}
                                {Object.keys(r.settings).length
                                    ? ` · ${Object.entries(r.settings)
                                          .map(([k, v]) => `${k}=${String(v)}`)
                                          .join(", ")}`
                                    : ""}{" "}
                                · {r.scope} · {r.requested_via === "schedule" ? "scheduled" : (r.requested_by ?? "user")}
                            </span>
                            {r.finished_at && r.created_at && (
                                <span className="text-gray-500">
                                    took {duration(Date.parse(r.finished_at) - Date.parse(r.created_at))}
                                </span>
                            )}
                            {r.status === "running" && <ToastToggle run={r} />}
                            {r.status === "running" &&
                                (r.skip_position != null ? (
                                    <span className="text-amber-300">skipping…</span>
                                ) : (
                                    <button
                                        type="button"
                                        className="border border-gray-700 hover:bg-gray-800 px-1.5 rounded-sm"
                                        title="Give up on the collection in flight and carry on with the next (Cancel stops the whole sweep)"
                                        onClick={() => void skipCurrent(r.id)}
                                    >
                                        Skip current
                                    </button>
                                ))}
                            {(r.status === "running" || r.status === "interrupted") && (
                                <button
                                    type="button"
                                    className="border border-gray-700 hover:bg-gray-800 px-1.5 rounded-sm"
                                    onClick={() =>
                                        void viewerApi
                                            .adminCancelAssetSweep(r.id)
                                            .then(() => load())
                                            .catch((e) => setError(errText(e)))
                                    }
                                >
                                    {r.status === "interrupted" ? "Close" : "Cancel"}
                                </button>
                            )}
                            {r.error && <span className="text-red-300">{r.error}</span>}
                        </div>
                        {open?.id === r.id && (
                            <SweepItems
                                run={open}
                                onSkip={(position) => void skip(r.id, position)}
                                onToggleRun={(position, on) => void toggleRun(r.id, position, on)}
                            />
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
};

export default ProviderSweepRuns;
