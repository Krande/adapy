import React, {useCallback, useEffect, useRef, useState} from "react";

import {itemChoiceLines, type CollectionChoiceDecl} from "@/assets/collectionChoices";
import type {SweepItem, SweepRun} from "@/services/viewerApi";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {sweepSkipTarget, trackSweep, untrackSweep} from "@/services/sweepTracking";
import {useRunFocusStore} from "@/state/runFocusStore";
import {useConversionStore} from "@/state/conversionStore";

import {
    NO_FULL_EXPORT,
    applyChangesOf,
    countsLine,
    queuedByApplyRunOf,
    queuedFullExportOf,
    runWarningsOf,
    sweepCountsText,
    sweepTargetsOf,
    prebuildLine,
    prebuildOf,
} from "./applyChangesItem";

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
    [NO_FULL_EXPORT]: "text-amber-300",
    // Another run of the provider holds the collection: waiting for it, or gave up waiting.
    waiting: "text-sky-300",
    busy: "text-amber-300",
};

const countsText = sweepCountsText;

/** An apply-changes run's run-level notes and its link to the full export it queued -- or, on that
 *  queued export, the link back. `onOpen` opens a linked run. */
export const RunNotes: React.FC<{run: SweepRun; onOpen: (id: string) => void}> = ({run, onOpen}) => {
    const warnings = runWarningsOf(run);
    const queued = queuedFullExportOf(run);
    const queuedBy = queuedByApplyRunOf(run);
    if (!warnings.length && !queued && !queuedBy) return null;
    const link = "underline decoration-dotted hover:text-white";
    return (
        <div className="space-y-0.5" data-testid="sweep-run-notes">
            {warnings.length > 0 && (
                <ul className="text-amber-300" data-testid="sweep-run-warnings">
                    {warnings.map((w) => (
                        <li key={w}>⚠ {w}</li>
                    ))}
                </ul>
            )}
            {queued && (
                <div className="text-blue-300" data-testid="sweep-run-follow-up">
                    Full project export queued for {queued.collections.join(", ") || "the collections left out"}:{" "}
                    <button type="button" className={link} onClick={() => onOpen(queued.runId)}>
                        open that run
                    </button>
                </div>
            )}
            {queuedBy && (
                <div className="text-gray-400" data-testid="sweep-run-queued-by">
                    Queued by an apply-changes run for collections without a full export:{" "}
                    <button type="button" className={link} onClick={() => onOpen(queuedBy)}>
                        open that run
                    </button>
                </div>
            )}
        </div>
    );
};

/** Whether an item's "run" box can still be changed: the sweep is running and has not reached it. */
export function canToggleRun(run: SweepRun, item: SweepItem): boolean {
    return run.status === "running" && (item.status === "pending" || item.status === "excluded");
}

/** An apply-changes item's own answer under its message: the element counts, `baseline` /
 *  `nothing`, the cursor moved (since → next), and each staging role's outcome. Nothing for a
 *  sweep's item, which carries no such block. */
const ApplyChangesResult: React.FC<{item: SweepItem}> = ({item}) => {
    const a = applyChangesOf(item);
    if (!a) return null;
    const badge = "px-1 rounded-sm border text-[10px] uppercase tracking-wide";
    return (
        <div className="mt-0.5 space-y-0.5" data-testid="apply-changes-result">
            <div className="flex flex-wrap items-center gap-1.5">
                {a.baseline && (
                    <span className={`${badge} border-blue-700 text-blue-300`} title="First run: only records where changes start from; nothing was applied">
                        baseline
                    </span>
                )}
                {a.nothing && (
                    <span className={`${badge} border-gray-700 text-gray-400`} title="Nothing changed since the last cursor">
                        nothing
                    </span>
                )}
                {a.counts && !a.baseline && <span className="text-gray-300">{countsLine(a.counts)}</span>}
                {!!a.counts?.unplaceable && (
                    <span className="text-amber-300" title="Elements the provider could not place in the tree">
                        {a.counts.unplaceable} unplaceable
                    </span>
                )}
            </div>
            {(a.since || a.nextCursor) && (
                <div className="font-mono text-gray-500" title="The cursor this run asked from, and the one it leaves for the next">
                    {a.since ?? "(none)"} → {a.nextCursor ?? "—"}
                </div>
            )}
            {a.roles.length > 0 && (
                <ul>
                    {a.roles.map((r) => (
                        <li key={r.role} className={r.applied ? "text-emerald-300" : r.status === "failed" ? "text-red-300" : "text-amber-300"}>
                            {r.role}: {r.status}
                            {r.warning && <span className="text-gray-400"> — {r.warning}</span>}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

const TARGET_TONE: Record<string, string> = {
    done: "text-emerald-300",
    unchanged: "text-emerald-300",
    nothing: "text-gray-400",
    error: "text-red-300",
    skipped: "text-amber-300",
    fetching: "text-blue-300",
};

/** A multi-target sweep item's outcome per target (tree, geometry, attributes, node bounds -- one
 *  provider job each, in order): its status, how long it took, its revision, and why it failed or
 *  was skipped. Nothing for an item that ran one job. */
const SweepTargets: React.FC<{item: SweepItem}> = ({item}) => {
    const targets = sweepTargetsOf(item);
    if (!targets.length) return null;
    return (
        <ul className="mt-0.5" data-testid="sweep-item-targets">
            {targets.map((t) => (
                <li key={t.target} className={TARGET_TONE[t.status] ?? "text-gray-400"}>
                    <span className="font-mono">{t.target}</span>: {t.status}
                    {t.totalMs != null && <span className="text-gray-500"> · {duration(t.totalMs)}</span>}
                    {t.revision && <span className="text-gray-500"> · {t.revision}</span>}
                    {t.note && <span className="text-gray-400 break-words"> — {t.note}</span>}
                </li>
            ))}
        </ul>
    );
};

/** Which per-collection choice (a share, say) the item's provider job was sent -- an admin's pick --
 *  or that the worker chose itself. Nothing when the provider declares no such choice. */
/** What "Pre-build meshes" did for the item: built / cached / failed of how many subjects, how long
 *  it took, or why it was not attempted; a note on what it does not cover; the first errors. */
const PrebuildResult: React.FC<{item: SweepItem}> = ({item}) => {
    const p = prebuildOf(item);
    if (!p) return null;
    return (
        <div className="text-[11px]" data-testid="sweep-item-prebuild">
            <span className={p.reason ? "text-amber-300" : p.failed ? "text-amber-300" : "text-emerald-300"}>{prebuildLine(p)}</span>
            {p.ms != null && <span className="text-gray-500"> · {duration(p.ms)}</span>}
            {p.note && <div className="text-gray-500">{p.note}</div>}
            {p.errors.length > 0 && <div className="text-red-300 break-words">{p.errors.join("; ")}</div>}
        </div>
    );
};

const ItemChoices: React.FC<{item: SweepItem; decls: CollectionChoiceDecl[]}> = ({item, decls}) => {
    const lines = itemChoiceLines(decls, item.collection, item.collection_choices);
    if (!lines.length) return null;
    return (
        <div className="text-[11px] text-sky-300" data-testid="sweep-item-choices">
            {lines.join(" · ")}
        </div>
    );
};

const SweepItems: React.FC<{
    run: SweepRun;
    choiceDecls: CollectionChoiceDecl[];
    onSkip: (position: number) => void;
    onToggleRun: (position: number, on: boolean) => void;
}> = ({run, choiceDecls, onSkip, onToggleRun}) => {
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
                                {i.status === NO_FULL_EXPORT ? (
                                    <span
                                        className="px-1 rounded-sm border border-amber-600 text-amber-300 text-[10px] uppercase tracking-wide"
                                        title={i.message ?? "This collection has not had a full project export yet; it was not asked for changes"}
                                        data-testid="no-full-export-badge"
                                    >
                                        no full export
                                    </span>
                                ) : (
                                    i.status
                                )}
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
                                ) : i.status === NO_FULL_EXPORT ? (
                                    <span className="text-amber-300 break-words">{i.message}</span>
                                ) : (
                                    <span className="text-gray-400 break-words">
                                        {i.revision ?? ""}
                                        {i.message ? `${i.revision ? " — " : ""}${i.message}` : ""}
                                    </span>
                                )}
                                <ApplyChangesResult item={i} />
                                <SweepTargets item={i} />
                                <ItemChoices item={i} decls={choiceDecls} />
                                <PrebuildResult item={i} />
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

const ProviderSweepRuns: React.FC<{
    provider: string;
    refreshKey: number;
    /** The provider's per-collection choices, to label what each item was sent. */
    choiceDecls?: CollectionChoiceDecl[];
}> = ({provider, refreshKey, choiceDecls = []}) => {
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

    // The history folds away: it only grows. Folded unless a sweep is running, or the user chose
    // (remembered per provider), or "View run" asked for one of these.
    const storageKey = `ada.admin.providers.sweepsOpen.${provider}`;
    const [openChoice, setOpenChoice] = useState<boolean | null>(() => {
        try {
            const raw = localStorage.getItem(storageKey);
            return raw === null ? null : raw === "1";
        } catch {
            return null;
        }
    });
    // A run opened (by "View run" too) always shows, whatever was chosen.
    const listOpen = open !== null || (openChoice ?? running);
    const toggleList = () => {
        const next = !listOpen;
        if (!next) setOpen(null);
        setOpenChoice(next);
        try {
            localStorage.setItem(storageKey, next ? "1" : "0");
        } catch {
            // Not remembered; still applied for this visit.
        }
    };
    const latest = runs[0];

    if (!runs.length && !error) return null;
    return (
        <div className="space-y-1" data-testid="provider-sweep-runs">
            <button
                type="button"
                className="flex items-center gap-2 text-[11px] text-gray-400 hover:text-white"
                aria-expanded={listOpen}
                title={listOpen ? "Fold the sweep history away" : "Show the sweep history"}
                onClick={toggleList}
            >
                <span className="text-gray-500">{listOpen ? "▾" : "▸"}</span>
                Sweeps ({runs.length})
                {!listOpen && latest && (
                    <span className="text-gray-500">
                        · latest {fmt(latest.created_at)} <span className={STATUS_TONE[latest.status] ?? ""}>{latest.status}</span>
                    </span>
                )}
            </button>
            {error && <div className="text-xs text-red-300">{error}</div>}
            {listOpen && (
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
                                · {r.scope} ·{" "}
                                {r.requested_via === "schedule"
                                    ? "scheduled"
                                    : r.requested_via === "apply-changes"
                                      ? "queued by apply-changes"
                                      : (r.requested_by ?? "user")}
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
                            {r.status === "interrupted" && (
                                <button
                                    type="button"
                                    className="border border-blue-700 text-blue-200 hover:bg-blue-900/40 px-1.5 rounded-sm"
                                    title="Go on from where it stopped: finished collections are kept, the ones in flight when it stopped run again"
                                    onClick={() =>
                                        void viewerApi
                                            .adminResumeAssetSweep(r.id)
                                            .then(() => {
                                                setError(null);
                                                return load().then(() => (open?.id === r.id ? openRun(r.id) : undefined));
                                            })
                                            .catch((e) => setError(errText(e)))
                                    }
                                >
                                    Resume
                                </button>
                            )}
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
                        {/* At the top of the run: what it left out and the export it queued. */}
                        <RunNotes
                            run={open?.id === r.id ? open : r}
                            onOpen={(id) =>
                                // Re-list first: a follow-up queued a moment ago may not be listed yet.
                                void load()
                                    .then(() => openRun(id))
                                    .then(() => rowRefs.current.get(id)?.scrollIntoView({block: "center", behavior: "smooth"}))
                            }
                        />
                        {open?.id === r.id && (
                            <SweepItems
                                run={open}
                                choiceDecls={choiceDecls}
                                onSkip={(position) => void skip(r.id, position)}
                                onToggleRun={(position, on) => void toggleRun(r.id, position, on)}
                            />
                        )}
                    </li>
                ))}
            </ul>
            )}
        </div>
    );
};

export default ProviderSweepRuns;
