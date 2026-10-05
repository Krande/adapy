import React, {useCallback, useEffect, useState} from "react";
import {AuditIssue, AuditIssueRecheckStart, viewerApi} from "@/services/viewerApi";

// Open audit issues on the configured forge, each with a Recheck button.
//
// A recheck re-runs every cell that ever reproduced the issue's fingerprint (a user's
// failed file through its failure-corpus copy, never the user's own scope) as an audit
// run; when that run finishes the issue-bot comments the verdict on the issue and closes
// it if every cell passed. "Recheck all" does the same for every open issue — the thing
// to press after a new worker image is deployed.

const VERDICT_STYLE: Record<string, {cls: string; label: string}> = {
    fixed: {cls: "bg-emerald-900/60 text-emerald-200", label: "fixed — closed"},
    reproduced: {cls: "bg-red-900/60 text-red-200", label: "still reproduces"},
    changed: {cls: "bg-amber-900/60 text-amber-200", label: "fails differently"},
    unverifiable: {cls: "bg-amber-900/60 text-amber-200", label: "inconclusive"},
    error: {cls: "bg-red-900/60 text-red-200", label: "bot error"},
};

// Poll while a recheck is running so its verdict shows up without a manual refresh.
const POLL_MS = 15_000;

function startSummary(s: AuditIssueRecheckStart): string {
    const parts: string[] = [];
    const cells = s.runs.reduce((n, r) => n + r.cells, 0);
    if (s.runs.length) parts.push(`started ${s.runs.length} run${s.runs.length > 1 ? "s" : ""} over ${cells} cell${cells === 1 ? "" : "s"}`);
    if (s.busy.length) parts.push(`${s.busy.length} already being rechecked`);
    if (s.no_cells.length) parts.push(`${s.no_cells.length} with no cell that can be re-run`);
    const skipped = Object.values(s.skipped).reduce((n, v) => n + v.length, 0);
    if (skipped) parts.push(`${skipped} cell${skipped === 1 ? "" : "s"} skipped`);
    return parts.join("; ") || "nothing to recheck";
}

const AuditIssuesSection: React.FC = () => {
    const [issues, setIssues] = useState<AuditIssue[] | null>(null);
    const [configured, setConfigured] = useState(true);
    const [err, setErr] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null); // fp, or "*" for recheck-all
    const [notice, setNotice] = useState<string | null>(null);
    const [skipped, setSkipped] = useState<Record<string, string[]>>({});

    const load = useCallback(async () => {
        try {
            const r = await viewerApi.adminAuditIssues();
            setConfigured(r.configured);
            setIssues(r.issues);
            setErr(null);
        } catch (e) {
            setErr((e as Error).message || "load failed");
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const anyRunning = !!issues?.some((i) => i.rechecking);
    useEffect(() => {
        if (!anyRunning) return;
        const t = window.setInterval(() => void load(), POLL_MS);
        return () => window.clearInterval(t);
    }, [anyRunning, load]);

    const recheck = useCallback(async (fp: string | null) => {
        setBusy(fp ?? "*");
        setNotice(null);
        try {
            const s = fp ? await viewerApi.adminAuditIssueRecheck(fp) : await viewerApi.adminAuditIssuesRecheckAll();
            setNotice(startSummary(s));
            setSkipped((prev) => ({...prev, ...s.skipped}));
            await load();
        } catch (e) {
            setNotice(`Recheck failed: ${(e as Error).message}`);
        } finally {
            setBusy(null);
        }
    }, [load]);

    if (!configured) return null;

    return (
        <div className="px-4 py-3 max-w-3xl space-y-3 border-t border-gray-700">
            <div className="flex items-center gap-3">
                <h2 className="text-sm font-semibold text-gray-100 flex-1">Open audit issues</h2>
                <button
                    type="button"
                    onClick={() => void load()}
                    className="text-xs text-gray-400 hover:text-gray-200"
                >
                    Refresh
                </button>
                <button
                    type="button"
                    onClick={() => void recheck(null)}
                    disabled={busy !== null || !issues?.length}
                    className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs px-2 py-1 rounded-sm"
                    title="Re-run the failing cells of every open issue, and close the ones that now pass"
                >
                    {busy === "*" ? "Starting…" : "Recheck all open"}
                </button>
            </div>
            <p className="text-xs text-gray-400">
                Recheck re-runs every cell that reproduced an issue — a user&apos;s file through its
                failure-corpus copy — and closes the issue when they all pass. A later failure with the
                same fingerprint reopens it.
            </p>
            {notice && <div className="text-xs text-gray-300" role="status">{notice}</div>}
            {err && <div className="text-xs text-red-400" role="alert">{err}</div>}
            {issues === null && !err && <div className="text-xs text-gray-500 italic">Loading…</div>}
            {issues?.length === 0 && <div className="text-xs text-gray-500">No open audit issues.</div>}
            {!!issues?.length && (
                <table className="w-full text-xs text-gray-300">
                    <thead className="text-gray-500 text-left">
                        <tr>
                            <th className="py-1 pr-2 font-normal">Issue</th>
                            <th className="py-1 pr-2 font-normal">Cells</th>
                            <th className="py-1 pr-2 font-normal">Last recheck</th>
                            <th className="py-1 font-normal" />
                        </tr>
                    </thead>
                    <tbody>
                        {issues.map((i) => {
                            const last = i.last_recheck;
                            const style = last?.verdict ? VERDICT_STYLE[last.verdict] : null;
                            return (
                                <tr key={i.fp} className="border-t border-gray-800 align-top">
                                    <td className="py-1 pr-2">
                                        {i.url ? (
                                            <a href={i.url} target="_blank" rel="noreferrer" className="text-blue-300 hover:underline">
                                                #{i.number}
                                            </a>
                                        ) : `#${i.number}`}{" "}
                                        <span className="text-gray-400">{i.title}</span>
                                        {skipped[i.fp]?.length ? (
                                            <ul className="mt-1 text-[11px] text-amber-300/80 list-disc pl-4">
                                                {skipped[i.fp].map((s) => <li key={s}>not re-run: {s}</li>)}
                                            </ul>
                                        ) : null}
                                    </td>
                                    <td className="py-1 pr-2 tabular-nums">{i.cells}</td>
                                    <td className="py-1 pr-2">
                                        {i.rechecking ? (
                                            <span className="text-gray-400 italic">running…</span>
                                        ) : style ? (
                                            <span title={last?.verdict_detail ?? ""} className={`px-1.5 py-0.5 rounded-sm ${style.cls}`}>
                                                {style.label}
                                            </span>
                                        ) : (
                                            <span className="text-gray-600">—</span>
                                        )}
                                        {last?.verdict_detail && !i.rechecking && (
                                            <div className="text-[11px] text-gray-500 mt-0.5">{last.verdict_detail}</div>
                                        )}
                                    </td>
                                    <td className="py-1 text-right">
                                        <button
                                            type="button"
                                            onClick={() => void recheck(i.fp)}
                                            disabled={busy !== null || i.rechecking}
                                            className="text-xs px-2 py-0.5 rounded-sm border border-gray-600 hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed"
                                        >
                                            {busy === i.fp ? "Starting…" : "Recheck"}
                                        </button>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            )}
        </div>
    );
};

export default AuditIssuesSection;
