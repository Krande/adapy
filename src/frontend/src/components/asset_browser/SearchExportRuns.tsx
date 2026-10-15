// The attribute search panel's Export runs: every export started from this scope, newest first,
// and -- opened -- what each of its items took and what each file came to.
//
// A MEASUREMENT AS MUCH AS A STATUS. As with a provider sweep (`ProviderSweepRuns.tsx`), an item's
// row shows its phases side by side -- the wait for a worker, then the job's own: resolving the
// hits, reading the source, writing the IFC, the attributes, the optional conversions, the
// thumbnails, the upload -- so the slow phase, and the slow collection, can be read off directly.
//
// ACTIONS. Cancel stops the whole run. Skip gives up on ONE running item and lets the rest go on
// -- offered per running item, since up to four run at once. "Open in gallery" walks the folder the
// run wrote, in its export order (`openFolderInGallery`), switching to the target scope if needed.
// "Rerun" drives the same run again over what did not finish -- with "Retry failed", also over what
// did not write every file; the server makes it idempotent. "Remove" drops a run that is not running
// from the list; its files stay. A file that did not come out (no geometry, or an error) is listed under its item, expandable.

import React, {useCallback, useEffect, useRef, useState} from "react";

import {loginSummary, rerunnable} from "@/assets/searchExport";
import {duration} from "@/components/admin/ProviderSweepRuns";
import {searchExportApi, type ExportItem, type ExportRun} from "@/services/api/searchExport";
import {ApiError} from "@/services/viewerApi";
import {openFolderInGallery} from "@/services/galleryFolder";
import {trackExport, untrackExport} from "@/services/sweepTracking";
import {useConversionStore} from "@/state/conversionStore";
import {useRunFocusStore} from "@/state/runFocusStore";

const POLL_MS = 5000;
const BTN = "border border-gray-700 hover:bg-gray-800 px-1.5 rounded-sm disabled:opacity-50";

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));
const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

const STATUS_TONE: Record<string, string> = {
    done: "text-emerald-300",
    partial: "text-amber-300",
    error: "text-red-300",
    skipped: "text-gray-500",
    pending: "text-gray-500",
    running: "text-blue-300",
    requesting: "text-violet-300",
    cancelled: "text-gray-400",
    interrupted: "text-amber-300",
};

/** The columns, each read off an item's timings. */
const COLUMNS: {key: string; title: string; hint: string; get: (i: ExportItem) => number | undefined}[] = [
    {
        key: "request",
        title: "Request",
        hint: "asking the design system for missing geometry: its request jobs, queued + run",
        get: (i) => i.timings.request_ms,
    },
    {key: "reqpub", title: "Req. publish", hint: "publishing what the requests answered", get: (i) => i.timings.request_publish_ms},
    {key: "queued", title: "Queued", hint: "waiting for a worker to take the job", get: (i) => i.timings.queued_ms},
    {key: "resolve", title: "Resolve", hint: "the job resolving the hits to source nodes", get: (i) => i.timings.job?.resolve_ms},
    {key: "read", title: "Read", hint: "reading the source", get: (i) => i.timings.job?.read_ms},
    {key: "ifc", title: "IFC", hint: "writing the IFC files", get: (i) => i.timings.job?.ifc_ms},
    {key: "attrs", title: "Attrs", hint: "writing the source attributes", get: (i) => i.timings.job?.attrs_ms},
    {key: "convert", title: "Convert", hint: "the optional GLB / STEP", get: (i) => i.timings.job?.convert_ms},
    {key: "thumb", title: "Thumb", hint: "rendering the thumbnails", get: (i) => i.timings.job?.thumb_ms},
    {key: "upload", title: "Upload", hint: "writing the files to the store", get: (i) => i.timings.job?.upload_ms},
    {key: "run", title: "Run", hint: "the job, start to done, as the API saw it", get: (i) => i.timings.run_ms},
    {key: "total", title: "Total", hint: "the whole item, queued to done", get: (i) => i.timings.total_ms},
];

function filesText(f: ExportRun["files"] | undefined): string {
    if (!f) return "";
    return [`${f.written} written`, f.no_geometry ? `${f.no_geometry} no geometry` : "", f.failed ? `${f.failed} failed` : ""]
        .filter(Boolean)
        .join(" · ");
}

function countsText(run: ExportRun): string {
    const c = run.counts ?? {};
    const finished = (c.done ?? 0) + (c.partial ?? 0) + (c.error ?? 0) + (c.skipped ?? 0);
    const parts = [`${finished}/${run.total} items`];
    if (c.requesting) parts.push(`${c.requesting} requesting geometry`);
    if (c.running) parts.push(`${c.running} running`);
    if (c.error) parts.push(`${c.error} failed`);
    return parts.join(" · ");
}

const ExportItems: React.FC<{run: ExportRun; onSkip: (position: number) => void}> = ({run, onSkip}) => {
    const items = run.items ?? [];
    const sums = COLUMNS.map((c) => items.reduce((acc, i) => acc + (c.get(i) ?? 0), 0));
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
                <thead className="text-left text-gray-500">
                    <tr>
                        <th className="px-1.5 py-1 font-normal">Collection</th>
                        <th className="px-1.5 py-1 font-normal text-right">Hits</th>
                        <th className="px-1.5 py-1 font-normal">Status</th>
                        {COLUMNS.map((c) => (
                            <th key={c.key} className="px-1.5 py-1 font-normal text-right" title={c.hint}>
                                {c.title}
                            </th>
                        ))}
                        <th className="px-1.5 py-1 font-normal">Files</th>
                    </tr>
                </thead>
                <tbody>
                    {items.map((i) => (
                        <tr key={i.position} className="border-t border-gray-800 align-top">
                            <td className="px-1.5 py-1 font-mono" title={i.provider}>
                                {i.collection}
                            </td>
                            <td className="px-1.5 py-1 text-right tabular-nums">{i.hits}</td>
                            <td className={`px-1.5 py-1 whitespace-nowrap ${STATUS_TONE[i.status] ?? ""}`}>
                                {i.status}
                                {i.status === "running" && i.progress != null && ` ${Math.round(i.progress * 100)}%`}
                                {run.skip_position === i.position && <span className="text-amber-300"> skipping…</span>}
                                {(i.status === "running" || i.status === "requesting") && run.status === "running" && run.skip_position == null && (
                                    <button
                                        type="button"
                                        className={`ml-1 ${BTN} text-gray-200`}
                                        title="Give up on this item and carry on with the rest (the run keeps going)"
                                        onClick={() => onSkip(i.position)}
                                    >
                                        Skip
                                    </button>
                                )}
                            </td>
                            {COLUMNS.map((c) => (
                                <td key={c.key} className="px-1.5 py-1 text-right tabular-nums">
                                    {duration(c.get(i))}
                                </td>
                            ))}
                            <td className="px-1.5 py-1 max-w-[22rem]">
                                <div className="text-gray-300">{filesText(i.files)}</div>
                                {(i.status === "running" || i.status === "requesting") && i.stage && (
                                    <div className="text-blue-300 break-words">{i.stage}</div>
                                )}
                                {loginSummary(i.timings.request_batches) && (
                                    <div
                                        className="text-gray-400"
                                        title={(i.timings.request_batches ?? [])
                                            .map(
                                                (b, n) =>
                                                    `job ${n + 1}: ${b.nodes} node(s), ${b.status ?? "in flight"}, login ${b.login_mode ?? "?"}` +
                                                    `${b.login_ms != null ? ` ${duration(b.login_ms)}` : ""}` +
                                                    `${b.export_ms != null ? `, export ${duration(b.export_ms)}` : ""}` +
                                                    `${b.publish_ms != null ? `, publish ${duration(b.publish_ms)}` : ""}` +
                                                    `${b.message ? ` -- ${b.message}` : ""}`,
                                            )
                                            .join("\n")}
                                    >
                                        {loginSummary(i.timings.request_batches)}
                                    </div>
                                )}
                                {i.error && <div className="text-red-300 break-words">{i.error}</div>}
                                {i.message && <div className="text-gray-400 break-words">{i.message}</div>}
                                {i.file_errors?.length > 0 && (
                                    <details>
                                        <summary className="cursor-pointer text-amber-300">
                                            {i.file_errors.length} file{i.file_errors.length === 1 ? "" : "s"} not written
                                        </summary>
                                        <ul className="mt-0.5 space-y-0.5">
                                            {i.file_errors.map((f) => (
                                                <li key={f.node_id} className="break-words">
                                                    <span className="font-mono text-gray-200">{f.full_name}</span>{" "}
                                                    <span className={f.status === "error" ? "text-red-300" : "text-gray-400"}>
                                                        {f.status === "no_geometry" ? "no geometry" : "error"}
                                                        {f.error ? `: ${f.error}` : ""}
                                                    </span>
                                                </li>
                                            ))}
                                        </ul>
                                    </details>
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
                            <td className="px-1.5 py-1">{filesText(run.files)}</td>
                        </tr>
                    </tfoot>
                )}
            </table>
        </div>
    );
};

/** Watch this export in the global toast (on from the start for one started here). */
const ToastToggle: React.FC<{run: ExportRun}> = ({run}) => {
    const on = useConversionStore((s) => !!s.jobs[`export:${run.id}`]);
    return (
        <label className="inline-flex items-center gap-1 text-gray-400" title="Show this export's progress in the global toast">
            <input
                type="checkbox"
                checked={on}
                onChange={(e) =>
                    e.target.checked
                        ? trackExport(run.scope, run.id, `Export ${run.hits} → ${run.target_scope}/${run.target_folder}`)
                        : untrackExport(run.id)
                }
            />
            toast
        </label>
    );
};

const SearchExportRuns: React.FC<{
    /** The searched scope whose runs to list. */
    scope: string;
    /** Bumped by the panel when it starts a run, to re-read and open it. */
    started: {id: string; n: number} | null;
}> = ({scope, started}) => {
    const [runs, setRuns] = useState<ExportRun[]>([]);
    const [open, setOpen] = useState<ExportRun | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [expanded, setExpanded] = useState(false);

    const load = useCallback(async () => {
        try {
            setRuns(await searchExportApi.searchExportRuns(scope, 20));
            setError(null);
        } catch (e) {
            setError(errText(e));
        }
    }, [scope]);

    const openRun = useCallback(
        async (id: string) => {
            try {
                setOpen(await searchExportApi.searchExportRun(scope, id));
            } catch (e) {
                setError(errText(e));
            }
        },
        [scope],
    );

    useEffect(() => {
        setOpen(null);
        void load();
    }, [load]);

    // A run just started here: show the list, open on it.
    useEffect(() => {
        if (!started) return;
        setExpanded(true);
        void load().then(() => openRun(started.id));
    }, [started, load, openRun]);

    // "View run" from the toast: open on that run once the list has it, scroll to it, clear the focus.
    const focus = useRunFocusStore((s) => s.focus);
    const rowRefs = useRef(new Map<string, HTMLLIElement>());
    useEffect(() => {
        if (focus?.kind !== "export" || focus.scope !== scope) return;
        const id = focus.runId;
        useRunFocusStore.getState().setFocus(null);
        setExpanded(true);
        void load()
            .then(() => openRun(id))
            .then(() => rowRefs.current.get(id)?.scrollIntoView({block: "center", behavior: "smooth"}));
    }, [focus, scope, load, openRun]);

    // Poll while anything is running, and keep the open run's items current.
    const running = runs.some((r) => r.status === "running");
    const openId = open?.id ?? null;
    useEffect(() => {
        if (!running || !expanded) return;
        const t = setInterval(() => {
            void load();
            if (openId) void openRun(openId);
        }, POLL_MS);
        return () => clearInterval(t);
    }, [running, expanded, openId, load, openRun]);

    /** The run whose rerun is being asked for: one request per click. */
    const [rerunning, setRerunning] = useState<string | null>(null);
    /** Per run, whether a rerun also retries failed items (default yes). */
    const [retry, setRetry] = useState<Record<string, boolean>>({});
    const retryFailed = (id: string) => retry[id] ?? true;
    /** `reopen`: refresh the open run afterwards -- not after removing it. */
    const act = async (fn: () => Promise<unknown>, id: string, reopen = true) => {
        try {
            await fn();
            setError(null);
        } catch (e) {
            setError(errText(e));
        }
        await load();
        if (reopen && open?.id === id) await openRun(id);
    };

    return (
        <div className="shrink-0 max-h-[45%] overflow-auto border-t border-gray-800" data-testid="search-export-runs">
            <button
                type="button"
                className="sticky top-0 z-10 flex w-full items-center gap-2 bg-gray-900 px-3 py-1.5 text-left text-gray-300 hover:text-white"
                onClick={() => setExpanded((v) => !v)}
            >
                <span>{expanded ? "▾" : "▸"}</span>
                <span className="text-[11px] uppercase tracking-wide">Export runs</span>
                {runs.length > 0 && <span className="text-gray-500">({runs.length})</span>}
                {running && <span className="text-blue-300">running</span>}
            </button>
            {expanded && (
                <div className="px-3 pb-2 space-y-1">
                    {error && <div className="text-red-300 break-words">{error}</div>}
                    {!runs.length && !error && <div className="text-gray-500">No exports from this scope yet.</div>}
                    <ul className="divide-y divide-gray-800 rounded-sm border border-gray-800">
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
                                    <span className="text-gray-400">
                                        {r.hits} hits · {filesText(r.files)}
                                    </span>
                                    <span className="font-mono text-gray-500 break-all">
                                        → {r.target_scope}/{r.target_folder}
                                    </span>
                                    {r.finished_at && r.created_at && (
                                        <span className="text-gray-500">took {duration(Date.parse(r.finished_at) - Date.parse(r.created_at))}</span>
                                    )}
                                    {r.status === "running" && <ToastToggle run={r} />}
                                    {r.status === "running" && (
                                        <button
                                            type="button"
                                            className={BTN}
                                            title="Stop the whole run (Skip on an item gives up on that one only)"
                                            onClick={() => void act(() => searchExportApi.cancelSearchExport(scope, r.id), r.id)}
                                        >
                                            Cancel
                                        </button>
                                    )}
                                    {r.skip_position != null && r.status === "running" && <span className="text-amber-300">skipping…</span>}
                                    {rerunnable(r) && (
                                        <span className="inline-flex items-center gap-1.5">
                                            <button
                                                type="button"
                                                className={BTN}
                                                disabled={rerunning === r.id || !rerunnable(r, retryFailed(r.id))}
                                                title={
                                                    retryFailed(r.id)
                                                        ? "Run again what did not finish and what did not write every file, into the same folder (a mesh run asks for fresh trees first)"
                                                        : "Run again only what did not finish, into the same folder"
                                                }
                                                onClick={() => {
                                                    setRerunning(r.id);
                                                    void act(async () => {
                                                        const out = await searchExportApi.rerunSearchExport(scope, r.id, retryFailed(r.id));
                                                        if (out.rerun) trackExport(scope, r.id, `Rerun ${r.target_scope}/${r.target_folder}`);
                                                        else if (out.reason) throw new Error(out.reason);
                                                    }, r.id).finally(() => setRerunning(null));
                                                }}
                                            >
                                                Rerun
                                            </button>
                                            <label className="inline-flex items-center gap-1 text-gray-400" title="Also retry items that finished without writing every file">
                                                <input
                                                    type="checkbox"
                                                    checked={retryFailed(r.id)}
                                                    onChange={(e) => setRetry((cur) => ({...cur, [r.id]: e.target.checked}))}
                                                />
                                                Retry failed
                                            </label>
                                        </span>
                                    )}
                                    <button
                                        type="button"
                                        className={BTN}
                                        disabled={!r.files?.written}
                                        title={`Walk the files written to ${r.target_scope}/${r.target_folder} in the gallery`}
                                        onClick={() => openFolderInGallery(r.target_scope, r.target_folder)}
                                    >
                                        Open in gallery
                                    </button>
                                    {r.status !== "running" && (
                                        <button
                                            type="button"
                                            className={BTN}
                                            title="Remove this run from the list. The files it wrote stay in the folder."
                                            onClick={() => {
                                                if (!window.confirm(`Remove the run into ${r.target_folder} from the list? Its files stay.`)) return;
                                                void act(async () => {
                                                    await searchExportApi.deleteSearchExport(scope, r.id);
                                                    untrackExport(r.id);
                                                    if (open?.id === r.id) setOpen(null);
                                                }, r.id, false);
                                            }}
                                        >
                                            Remove
                                        </button>
                                    )}
                                    {r.error && <span className="text-red-300 break-words">{r.error}</span>}
                                </div>
                                {open?.id === r.id && (
                                    <ExportItems
                                        run={open}
                                        onSkip={(position) => void act(() => searchExportApi.skipSearchExportItem(scope, r.id, position), r.id)}
                                    />
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
};

export default SearchExportRuns;
