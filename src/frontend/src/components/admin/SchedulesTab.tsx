import React, {useCallback, useEffect, useState} from "react";
import {AuditSchedule, Corpus, viewerApi} from "@/services/viewerApi";

// Admin tab — manage recurring audit schedules (M4 of the audit
// panel design in plan/v2/notes_admin_audit_panel.md).
//
// Each row pairs a cron expression with a (scope, worker_pool) sweep
// target. The API's scheduler tick claims due rows and fires the
// same dispatcher used by ``POST /admin/audit/runs``. This tab is
// pure CRUD + a "fire now" override; firing semantics live entirely
// server-side so the UI can't desync against the actual fire log.

function fmtTimestamp(iso: string | null): string {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
}

function fmtRelative(iso: string | null): string {
    if (!iso) return "";
    const ms = new Date(iso).getTime() - Date.now();
    const sign = ms >= 0 ? "in" : "ago";
    const abs = Math.abs(ms);
    if (abs < 60_000) return `${sign === "in" ? "in <1m" : "<1m ago"}`;
    if (abs < 3600_000) return `${sign} ${Math.round(abs / 60_000)}m`;
    if (abs < 86400_000) return `${sign} ${Math.round(abs / 3600_000)}h`;
    return `${sign} ${Math.round(abs / 86400_000)}d`;
}

// Common 5-field cron patterns the admin can pick from instead of
// hand-typing. Free-text input remains available — these are just
// shortcuts for the cases that account for ~all real usage.
const CRON_PRESETS: {label: string; expr: string}[] = [
    {label: "Every hour",        expr: "0 * * * *"},
    {label: "Every 4 hours",     expr: "0 */4 * * *"},
    {label: "Daily 02:00 UTC",   expr: "0 2 * * *"},
    {label: "Weekly (Mon 02:00)", expr: "0 2 * * 1"},
    {label: "Weekdays 02:00",    expr: "0 2 * * 1-5"},
];

const NewScheduleForm: React.FC<{
    corpora: Corpus[];
    capabilities: string[];
    onCreated: () => void;
}> = ({corpora, capabilities, onCreated}) => {
    const [name, setName] = useState("");
    const [cronExpr, setCronExpr] = useState("0 2 * * *");
    const [scope, setScope] = useState("shared");
    const [workerPool, setWorkerPool] = useState("");
    const [enabled, setEnabled] = useState(true);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const onSubmit = useCallback(async (e: React.FormEvent) => {
        e.preventDefault();
        setErr(null);
        if (!name.trim()) {
            setErr("name required");
            return;
        }
        setBusy(true);
        try {
            await viewerApi.adminAuditScheduleCreate({
                name: name.trim(),
                cron_expr: cronExpr.trim(),
                scope,
                worker_pool: workerPool.trim() || null,
                enabled,
            });
            setName("");
            // Keep cron/scope/pool so the next add can be a quick variant.
            onCreated();
        } catch (e) {
            setErr((e as Error).message || "create failed");
        } finally {
            setBusy(false);
        }
    }, [name, cronExpr, scope, workerPool, enabled, onCreated]);

    return (
        <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2 px-3 py-2 border-b border-gray-800 bg-gray-900/40">
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Name</span>
                <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="nightly cad-baseline"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 w-56"
                />
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Cron expression</span>
                <input
                    type="text"
                    value={cronExpr}
                    onChange={(e) => setCronExpr(e.target.value)}
                    placeholder="0 2 * * *"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-40"
                    title="5-field UTC cron: minute hour dom month dow"
                />
                <select
                    value=""
                    onChange={(e) => { if (e.target.value) setCronExpr(e.target.value); }}
                    className="bg-gray-900 border border-gray-700 rounded-sm px-1 py-0.5 text-[10px] text-gray-400"
                    title="Common presets"
                >
                    <option value="">presets…</option>
                    {CRON_PRESETS.map((p) => (
                        <option key={p.expr} value={p.expr}>{p.label} — {p.expr}</option>
                    ))}
                </select>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Scope</span>
                <select
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-56"
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
                    </optgroup>
                </select>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Worker pool</span>
                <select
                    value={workerPool}
                    onChange={(e) => setWorkerPool(e.target.value)}
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-40"
                >
                    <option value="">any pool</option>
                    {capabilities.map((c) => (
                        <option key={c} value={c}>{c}</option>
                    ))}
                </select>
            </label>
            <label className="text-xs text-gray-300 flex items-center gap-1 h-[30px] mt-auto">
                <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(e) => setEnabled(e.target.checked)}
                    className="accent-blue-600"
                />
                <span>Enabled</span>
            </label>
            <button
                type="submit"
                disabled={busy}
                className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm px-3 py-1 rounded-sm h-[30px]"
            >
                {busy ? "Saving…" : "Add schedule"}
            </button>
            {err && (
                <div className="w-full text-xs text-red-400" role="alert">{err}</div>
            )}
        </form>
    );
};

const ScheduleRow: React.FC<{
    schedule: AuditSchedule;
    onChanged: () => void;
}> = ({schedule, onChanged}) => {
    const [busy, setBusy] = useState<string | null>(null);
    const [err, setErr] = useState<string | null>(null);

    const toggleEnabled = useCallback(async () => {
        setBusy("toggle");
        setErr(null);
        try {
            await viewerApi.adminAuditScheduleUpdate(schedule.id, {
                enabled: !schedule.enabled,
            });
            onChanged();
        } catch (e) {
            setErr((e as Error).message || "toggle failed");
        } finally {
            setBusy(null);
        }
    }, [schedule.id, schedule.enabled, onChanged]);

    const fireNow = useCallback(async () => {
        setBusy("fire");
        setErr(null);
        try {
            await viewerApi.adminAuditScheduleFireNow(schedule.id);
            onChanged();
        } catch (e) {
            setErr((e as Error).message || "fire failed");
        } finally {
            setBusy(null);
        }
    }, [schedule.id, onChanged]);

    const archive = useCallback(async () => {
        if (!window.confirm(`Archive schedule "${schedule.name}"? It will stop firing immediately.`)) {
            return;
        }
        setBusy("archive");
        setErr(null);
        try {
            await viewerApi.adminAuditScheduleArchive(schedule.id);
            onChanged();
        } catch (e) {
            setErr((e as Error).message || "archive failed");
        } finally {
            setBusy(null);
        }
    }, [schedule.id, schedule.name, onChanged]);

    return (
        <div className={
            "border-b border-gray-800 px-3 py-2 text-xs " +
            (schedule.enabled ? "" : "opacity-60")
        }>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <div className="font-medium text-gray-100 min-w-0 truncate">
                    {schedule.name}
                </div>
                <code className="font-mono text-gray-400">{schedule.cron_expr}</code>
                <div className="text-gray-400 font-mono truncate">
                    {schedule.scope}
                </div>
                {schedule.worker_pool && (
                    <div className="text-gray-500 font-mono">pool:{schedule.worker_pool}</div>
                )}
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-gray-500">
                <span>
                    next: <span className="text-gray-300">{fmtTimestamp(schedule.next_fire_at)}</span>{" "}
                    <span className="text-gray-500">({fmtRelative(schedule.next_fire_at)})</span>
                </span>
                <span>
                    last: <span className="text-gray-300">{fmtTimestamp(schedule.last_fired_at)}</span>
                </span>
            </div>
            {schedule.last_skipped_reason && (
                <div className="mt-1 text-amber-300 text-[11px]">
                    skip note: {schedule.last_skipped_reason}
                </div>
            )}
            {err && (
                <div className="mt-1 text-red-400 text-[11px]" role="alert">{err}</div>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
                <button
                    type="button"
                    onClick={toggleEnabled}
                    disabled={busy !== null}
                    className={
                        "px-2 py-0.5 rounded-sm text-[11px] border disabled:opacity-50 " +
                        (schedule.enabled
                            ? "border-amber-700 text-amber-300 hover:bg-amber-900/30"
                            : "border-emerald-700 text-emerald-300 hover:bg-emerald-900/30")
                    }
                >
                    {schedule.enabled ? "Disable" : "Enable"}
                </button>
                <button
                    type="button"
                    onClick={fireNow}
                    disabled={busy !== null || !schedule.enabled}
                    className="px-2 py-0.5 rounded-sm text-[11px] border border-blue-700 text-blue-300 hover:bg-blue-900/30 disabled:opacity-50"
                    title={schedule.enabled
                        ? "Dispatch this sweep right now, regardless of the cron slot"
                        : "Re-enable the schedule first"}
                >
                    {busy === "fire" ? "Firing…" : "Fire now"}
                </button>
                <button
                    type="button"
                    onClick={archive}
                    disabled={busy !== null}
                    className="px-2 py-0.5 rounded-sm text-[11px] border border-red-700 text-red-300 hover:bg-red-900/30 disabled:opacity-50"
                >
                    Archive
                </button>
            </div>
        </div>
    );
};

const SchedulesTab: React.FC = () => {
    const [schedules, setSchedules] = useState<AuditSchedule[]>([]);
    const [corpora, setCorpora] = useState<Corpus[]>([]);
    const [capabilities, setCapabilities] = useState<string[]>([]);
    const [listError, setListError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const r = await viewerApi.adminAuditSchedulesList();
            setSchedules(r.schedules);
            setListError(null);
        } catch (e) {
            setListError((e as Error).message || "failed to load schedules");
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    // Mirror AuditRunsTab — the same scope picker and pool picker
    // belong here, so reuse the same source-of-truth fetches.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const r = await viewerApi.adminCorporaList();
                if (!cancelled) setCorpora(r.corpora);
            } catch {
                // Non-fatal — picker still has shared.
            }
        })();
        (async () => {
            try {
                const r = await viewerApi.adminListWorkers();
                if (cancelled) return;
                const tags = new Set<string>();
                for (const w of r.workers) {
                    if (!w.online) continue;
                    for (const c of w.capabilities || []) {
                        const v = c.trim().toLowerCase();
                        if (v) tags.add(v);
                    }
                }
                setCapabilities(Array.from(tags).sort());
            } catch {
                // Non-fatal — pool picker collapses to "any".
            }
        })();
        return () => { cancelled = true; };
    }, []);

    return (
        <div className="flex flex-col h-full">
            <NewScheduleForm
                corpora={corpora}
                capabilities={capabilities}
                onCreated={load}
            />
            <div className="flex-1 overflow-auto">
                {listError && (
                    <div className="text-xs text-red-400 px-3 py-2">{listError}</div>
                )}
                {schedules.length === 0 && !listError && (
                    <div className="text-xs text-gray-500 italic px-3 py-4">
                        No schedules yet. Add one above to start firing audits
                        on a cron pattern.
                    </div>
                )}
                {schedules.map((s) => (
                    <ScheduleRow key={s.id} schedule={s} onChanged={load}/>
                ))}
            </div>
        </div>
    );
};

export default SchedulesTab;
