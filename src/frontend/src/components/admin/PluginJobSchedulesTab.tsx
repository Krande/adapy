import React, {useCallback, useEffect, useMemo, useState} from "react";

import {
    AdminProject,
    ApiError,
    BackendPluginSpec,
    PluginJobSchedule,
    viewerApi,
} from "@/services/viewerApi";

import {parseScheduleOptions} from "./pluginScheduleOptions";
import {CRON_PRESETS, fmtRelative, fmtTimestamp} from "./scheduleFormat";

// Admin panel — recurring plugin jobs.
//
// WHY THIS EXISTS AT ALL. A plugin job that should run on a timer previously had
// to be driven by a cron on the machine the worker runs on, POSTing to this API:
// that machine then holds a credential able to enqueue, the schedule is invisible
// here, and changing when it runs means logging into the box. For a plugin
// driving a licensed workstation all three are the wrong answer. A row here is
// enqueued by the API's own tick, through exactly the path
// ``POST /plugins/{id}/jobs`` uses, so the worker cannot tell a scheduled firing
// from a user-initiated one.
//
// Firing semantics are entirely server-side — the concurrent-fire guard, scope
// resolution at fire time, the timestamp stamped into the options so two firings
// never share a cache key. This panel is CRUD plus a "Run now" override, so it
// cannot drift from what actually fires.

function errText(e: unknown, fallback: string): string {
    if (e instanceof ApiError) return e.detail || e.message;
    return (e as Error)?.message || fallback;
}

const INPUT = "bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100";

/** The cron field: free text, with a preset picker beside it. */
const CronField: React.FC<{
    value: string;
    onChange: (v: string) => void;
}> = ({value, onChange}) => (
    <>
        <input
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder="0 * * * *"
            className={`${INPUT} font-mono w-40`}
            title="5-field UTC cron: minute hour dom month dow"
        />
        <select
            value=""
            onChange={(e) => {
                if (e.target.value) onChange(e.target.value);
            }}
            className="bg-gray-900 border border-gray-700 rounded-sm px-1 py-0.5 text-[10px] text-gray-400"
            title="Common presets"
        >
            <option value="">presets…</option>
            {CRON_PRESETS.map((p) => (
                <option key={p.expr} value={p.expr}>
                    {p.label} — {p.expr}
                </option>
            ))}
        </select>
    </>
);

/** Scope picker: the projects this admin can see, plus shared.
 *
 * Free text as well as a list, because the wire format accepts scopes this list
 * cannot enumerate (a corpus, or a project that exists but is archived), and a
 * picker that silently cannot express a valid value is worse than a text box. */
const ScopeField: React.FC<{
    value: string;
    onChange: (v: string) => void;
    projects: AdminProject[];
}> = ({value, onChange, projects}) => (
    <>
        <input
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            list="plugin-schedule-scopes"
            placeholder="project:my-project"
            className={`${INPUT} font-mono w-56`}
            title='Wire-format scope: "shared", or "project:<slug>"'
        />
        <datalist id="plugin-schedule-scopes">
            <option value="shared"/>
            {projects.map((p) => (
                <option key={p.slug} value={`project:${p.slug}`}>
                    {p.name}
                </option>
            ))}
        </datalist>
    </>
);

const NewScheduleForm: React.FC<{
    plugins: BackendPluginSpec[];
    projects: AdminProject[];
    onCreated: () => void;
}> = ({plugins, projects, onCreated}) => {
    const [name, setName] = useState("");
    const [cronExpr, setCronExpr] = useState("0 * * * *");
    const [scope, setScope] = useState("shared");
    const [pluginId, setPluginId] = useState("");
    const [optionsText, setOptionsText] = useState("{}");
    const [capability, setCapability] = useState("");
    const [enabled, setEnabled] = useState(true);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const parsed = useMemo(() => parseScheduleOptions(optionsText), [optionsText]);
    const optionsError = "error" in parsed ? parsed.error : null;

    const onSubmit = useCallback(
        async (e: React.FormEvent) => {
            e.preventDefault();
            setErr(null);
            if (!name.trim()) return setErr("name required");
            if (!pluginId.trim()) return setErr("plugin required");
            if (!scope.trim()) return setErr("scope required");
            if ("error" in parsed) return setErr(`options: ${parsed.error}`);
            setBusy(true);
            try {
                await viewerApi.adminPluginJobScheduleCreate({
                    name: name.trim(),
                    cron_expr: cronExpr.trim(),
                    scope: scope.trim(),
                    plugin_id: pluginId.trim(),
                    options: parsed.options,
                    capability: capability.trim() || null,
                    enabled,
                });
                // Only the name is cleared: the next schedule added is usually a
                // variant of this one, and retyping the options document is the
                // expensive part.
                setName("");
                onCreated();
            } catch (e) {
                setErr(errText(e, "create failed"));
            } finally {
                setBusy(false);
            }
        },
        [name, cronExpr, scope, pluginId, parsed, capability, enabled, onCreated],
    );

    return (
        <form
            onSubmit={onSubmit}
            className="flex flex-wrap items-end gap-2 px-3 py-2 border-b border-gray-800 bg-gray-900/40"
        >
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Name</span>
                <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="nightly refresh"
                    className={`${INPUT} w-56`}
                />
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Plugin</span>
                <input
                    type="text"
                    value={pluginId}
                    onChange={(e) => setPluginId(e.target.value)}
                    list="plugin-schedule-plugins"
                    placeholder="plugin id"
                    className={`${INPUT} font-mono w-48`}
                    title="An id no live worker serves is accepted: a schedule may outlive a pool being down"
                />
                <datalist id="plugin-schedule-plugins">
                    {plugins.map((p) => (
                        <option key={p.slug} value={p.slug}>
                            {p.title || p.slug}
                        </option>
                    ))}
                </datalist>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Cron expression</span>
                <CronField value={cronExpr} onChange={setCronExpr}/>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Scope</span>
                <ScopeField value={scope} onChange={setScope} projects={projects}/>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Capability</span>
                <input
                    type="text"
                    value={capability}
                    onChange={(e) => setCapability(e.target.value)}
                    placeholder="from plugin spec"
                    className={`${INPUT} font-mono w-40`}
                    title="Pool override. Empty routes to whatever the plugin's live spec advertises."
                />
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
            <label className="text-xs text-gray-300 flex flex-col gap-1 w-full">
                <span>
                    Options <span className="text-gray-500">(JSON, handed to the plugin verbatim)</span>
                </span>
                <textarea
                    value={optionsText}
                    onChange={(e) => setOptionsText(e.target.value)}
                    rows={3}
                    spellCheck={false}
                    className={`${INPUT} font-mono w-full text-xs`}
                />
            </label>
            {optionsError && (
                <div className="w-full text-xs text-amber-300" role="alert">
                    options: {optionsError}
                </div>
            )}
            <button
                type="submit"
                disabled={busy || optionsError !== null}
                className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm px-3 py-1 rounded-sm h-[30px]"
            >
                {busy ? "Saving…" : "Add schedule"}
            </button>
            {err && (
                <div className="w-full text-xs text-red-400" role="alert">
                    {err}
                </div>
            )}
        </form>
    );
};

/** Inline editor for an existing row.
 *
 * Every field a schedule has, rather than enable/disable only: the whole point
 * of the feature is that retiming a job does not mean logging into the machine
 * the worker runs on, and "delete it and make a new one" is not retiming — it
 * loses the firing history the row carries. */
const EditForm: React.FC<{
    schedule: PluginJobSchedule;
    projects: AdminProject[];
    onDone: () => void;
    onCancel: () => void;
}> = ({schedule, projects, onDone, onCancel}) => {
    const [cronExpr, setCronExpr] = useState(schedule.cron_expr);
    const [scope, setScope] = useState(schedule.scope);
    const [capability, setCapability] = useState(schedule.capability ?? "");
    const [optionsText, setOptionsText] = useState(() => JSON.stringify(schedule.options ?? {}, null, 2));
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const parsed = useMemo(() => parseScheduleOptions(optionsText), [optionsText]);
    const optionsError = "error" in parsed ? parsed.error : null;

    const save = useCallback(
        async (e: React.FormEvent) => {
            e.preventDefault();
            setErr(null);
            if ("error" in parsed) return setErr(`options: ${parsed.error}`);
            setBusy(true);
            try {
                // Sends every field, not a diff. The PATCH only writes keys that
                // are present, and a diff would have to decide what "unchanged"
                // means for an options document — a false negative there is a
                // saved edit that silently did not save.
                await viewerApi.adminPluginJobScheduleUpdate(schedule.id, {
                    cron_expr: cronExpr.trim(),
                    scope: scope.trim(),
                    capability: capability.trim() || null,
                    options: parsed.options,
                });
                onDone();
            } catch (e) {
                setErr(errText(e, "save failed"));
            } finally {
                setBusy(false);
            }
        },
        [schedule.id, cronExpr, scope, capability, parsed, onDone],
    );

    return (
        <form onSubmit={save} className="mt-2 flex flex-wrap items-end gap-2 border-t border-gray-800 pt-2">
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Cron expression</span>
                <CronField value={cronExpr} onChange={setCronExpr}/>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Scope</span>
                <ScopeField value={scope} onChange={setScope} projects={projects}/>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Capability</span>
                <input
                    type="text"
                    value={capability}
                    onChange={(e) => setCapability(e.target.value)}
                    placeholder="from plugin spec"
                    className={`${INPUT} font-mono w-40`}
                />
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1 w-full">
                <span>Options (JSON)</span>
                <textarea
                    value={optionsText}
                    onChange={(e) => setOptionsText(e.target.value)}
                    rows={4}
                    spellCheck={false}
                    className={`${INPUT} font-mono w-full text-xs`}
                />
            </label>
            {optionsError && (
                <div className="w-full text-[11px] text-amber-300" role="alert">
                    options: {optionsError}
                </div>
            )}
            {err && (
                <div className="w-full text-[11px] text-red-400" role="alert">
                    {err}
                </div>
            )}
            <div className="flex gap-2">
                <button
                    type="submit"
                    disabled={busy || optionsError !== null}
                    className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white text-[11px] px-2 py-1 rounded-sm"
                >
                    {busy ? "Saving…" : "Save"}
                </button>
                <button
                    type="button"
                    onClick={onCancel}
                    disabled={busy}
                    className="px-2 py-1 rounded-sm text-[11px] border border-gray-600 text-gray-300 hover:bg-gray-800 disabled:opacity-50"
                >
                    Cancel
                </button>
            </div>
        </form>
    );
};

const ScheduleRow: React.FC<{
    schedule: PluginJobSchedule;
    projects: AdminProject[];
    onChanged: () => void;
}> = ({schedule, projects, onChanged}) => {
    const [busy, setBusy] = useState<string | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [note, setNote] = useState<string | null>(null);
    const [editing, setEditing] = useState(false);

    const act = useCallback(
        async (label: string, fn: () => Promise<string | null>) => {
            setBusy(label);
            setErr(null);
            setNote(null);
            try {
                const message = await fn();
                if (message) setNote(message);
                onChanged();
            } catch (e) {
                setErr(errText(e, `${label} failed`));
            } finally {
                setBusy(null);
            }
        },
        [onChanged],
    );

    const toggleEnabled = () =>
        act("toggle", async () => {
            await viewerApi.adminPluginJobScheduleUpdate(schedule.id, {enabled: !schedule.enabled});
            return null;
        });

    const runNow = () =>
        act("run", async () => {
            const r = await viewerApi.adminPluginJobScheduleRunNow(schedule.id);
            // The job id is the whole value of pressing this: it is what the
            // operator takes to the Audit log to watch the run they just caused.
            return `queued job ${r.job_id}`;
        });

    const archive = () => {
        if (!window.confirm(`Archive schedule "${schedule.name}"? It will stop firing immediately.`)) return;
        void act("archive", async () => {
            await viewerApi.adminPluginJobScheduleArchive(schedule.id);
            return null;
        });
    };

    const optionKeys = Object.keys(schedule.options ?? {});

    return (
        <div className={"border-b border-gray-800 px-3 py-2 text-xs " + (schedule.enabled ? "" : "opacity-60")}>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <div className="font-medium text-gray-100 min-w-0 truncate">{schedule.name}</div>
                <code className="font-mono text-gray-400">{schedule.cron_expr}</code>
                <div className="text-blue-300 font-mono truncate">{schedule.plugin_id}</div>
                <div className="text-gray-400 font-mono truncate">{schedule.scope}</div>
                {schedule.capability && (
                    <div className="text-gray-500 font-mono">cap:{schedule.capability}</div>
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
                {schedule.last_job_id && (
                    <span>
                        job: <code className="font-mono text-gray-400">{schedule.last_job_id}</code>
                    </span>
                )}
                {optionKeys.length > 0 && (
                    <span title={JSON.stringify(schedule.options, null, 2)}>
                        options: <span className="text-gray-400 font-mono">{optionKeys.join(", ")}</span>
                    </span>
                )}
            </div>
            {/* A skipped slot is the failure this whole feature exists to make
                visible: without it, a schedule that quietly does nothing looks
                exactly like one that fired and failed somewhere else. */}
            {schedule.last_skipped_reason && (
                <div className="mt-1 text-amber-300 text-[11px]">skip note: {schedule.last_skipped_reason}</div>
            )}
            {note && <div className="mt-1 text-emerald-300 text-[11px]">{note}</div>}
            {err && (
                <div className="mt-1 text-red-400 text-[11px]" role="alert">
                    {err}
                </div>
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
                    onClick={runNow}
                    disabled={busy !== null}
                    className="px-2 py-0.5 rounded-sm text-[11px] border border-blue-700 text-blue-300 hover:bg-blue-900/30 disabled:opacity-50"
                    title="Enqueue this job now. Does not disturb the timetable — the scheduled slot still happens."
                >
                    {busy === "run" ? "Running…" : "Run now"}
                </button>
                <button
                    type="button"
                    onClick={() => setEditing((v) => !v)}
                    disabled={busy !== null}
                    className="px-2 py-0.5 rounded-sm text-[11px] border border-gray-600 text-gray-300 hover:bg-gray-800 disabled:opacity-50"
                >
                    {editing ? "Close" : "Edit"}
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
            {editing && (
                <EditForm
                    schedule={schedule}
                    projects={projects}
                    onDone={() => {
                        setEditing(false);
                        onChanged();
                    }}
                    onCancel={() => setEditing(false)}
                />
            )}
        </div>
    );
};

const PluginJobSchedulesSection: React.FC = () => {
    const [schedules, setSchedules] = useState<PluginJobSchedule[]>([]);
    const [plugins, setPlugins] = useState<BackendPluginSpec[]>([]);
    const [projects, setProjects] = useState<AdminProject[]>([]);
    const [listError, setListError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const r = await viewerApi.adminPluginJobSchedulesList();
            setSchedules(r.schedules);
            setListError(null);
        } catch (e) {
            setListError(errText(e, "failed to load schedules"));
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    // Both pickers are suggestions, so neither fetch is fatal: a plugin id and a
    // scope can be typed, and the API accepts values these lists cannot offer.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const r = await viewerApi.listBackendPlugins();
                if (!cancelled) setPlugins(r.plugins);
            } catch {
                /* suggestions only */
            }
        })();
        (async () => {
            try {
                const r = await viewerApi.adminListProjects();
                if (!cancelled) setProjects(r.filter((p) => !p.archived_at));
            } catch {
                /* suggestions only */
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    return (
        <div className="flex flex-col">
            <NewScheduleForm plugins={plugins} projects={projects} onCreated={load}/>
            {listError && <div className="text-xs text-red-400 px-3 py-2">{listError}</div>}
            {schedules.length === 0 && !listError && (
                <div className="text-xs text-gray-500 italic px-3 py-4">
                    No plugin jobs scheduled. Add one above to run a plugin on a cron pattern —
                    the API fires it, so nothing has to be configured on the worker's machine.
                </div>
            )}
            {schedules.map((s) => (
                <ScheduleRow key={s.id} schedule={s} projects={projects} onChanged={load}/>
            ))}
        </div>
    );
};

export default PluginJobSchedulesSection;
