import React, {useCallback, useEffect, useMemo, useState} from "react";

import {changedCount} from "@/assets/changeRuns";
import type {
    AssetSchedulesListing,
    ProviderSchedule,
    ProviderScheduleJob,
    ProviderSchedules,
    ScheduleChoice,
    ScheduleSetting,
} from "@/services/viewerApi";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {sortProjects} from "@/utils/sortProjects";

import AdminSection from "./AdminSection";
import ProviderSweepRuns from "./ProviderSweepRuns";
import {choiceKey, choiceLabel, selectedKey, valueOfKey} from "./scheduleSettings";
import SweepDefaults from "./SweepDefaults";

// Admin → Providers → Scheduled jobs: the jobs each asset provider declares on its plugin spec
// (`asset_schedules`), scheduled per collection.
//
// NO SCOPE. A provider job always runs in the provider store, whoever or whatever starts it, so a schedule or
// a sweep names no scope; scopes read the store's data through Access grants (the Access section).
//
// EVERY FIELD IS A CHOICE. Job, collection, frequency and each setting are dropdowns of what
// the provider and the server offer, and the server re-checks every value -- this replaces the
// generic plugin-job schedule form, whose options were free text. A setting with no choices is not
// offered at all.
//
// A change check's last run shows here; its nodes are inspected in the Sources tab ("Changes").
// Report only: nothing a check finds is published automatically.
//
// A SWEEP is the exception, by declaration: a provider's `sweep` entry asks for every collection
// it offers and publishes each, so its schedule names no collection, it can also be run once
// without saving a schedule ("Run once now"), and its runs -- with every phase timed -- list
// under the provider (ProviderSweepRuns).

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));
const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

function lastRunText(s: ProviderSchedule): {text: string; tone: string} {
    const run = s.last_run;
    if (s.last_skipped_reason) return {text: `skipped: ${s.last_skipped_reason}`, tone: "text-amber-300"};
    if (s.kind === "sweep") {
        return s.last_fired_at || s.last_job_id
            ? {text: `swept ${fmt(s.last_fired_at)}; see Sweeps below`, tone: "text-gray-300"}
            : {text: "not run yet", tone: "text-gray-500"};
    }
    if (!run) return {text: s.last_job_id ? "fired; no change-check record" : "not run yet", tone: "text-gray-500"};
    if (run.status === "queued") return {text: `running since ${fmt(run.created_at)}`, tone: "text-gray-300"};
    if (run.status === "error") return {text: `failed: ${run.error ?? "unknown error"}`, tone: "text-red-300"};
    if (run.up_to_date) return {text: `up to date (${fmt(run.created_at)})`, tone: "text-emerald-300"};
    const n = changedCount(run);
    return {
        text: `${n !== null ? `${n} changed` : "changed"} (${fmt(run.created_at)})${run.message ? ` — ${run.message}` : ""}`,
        tone: run.stale ? "text-amber-300" : "text-gray-300",
    };
}

function settingSummary(job: ProviderScheduleJob | undefined, settings: Record<string, unknown>): string {
    const parts = (job?.settings ?? [])
        .map((s) => {
            const v = settings[s.name];
            if (v == null || v === "" || (Array.isArray(v) && !v.length)) return null;
            const label = (x: unknown) => choiceLabel(x, s.choices);
            return `${s.title}: ${Array.isArray(v) ? v.map(label).join(", ") : label(v)}`;
        })
        .filter(Boolean);
    return parts.length ? parts.join(" · ") : "—";
}

/** One setting as a select (or a multi-select for a list). Never a text box. */
const SettingField: React.FC<{
    setting: ScheduleSetting;
    choices: ScheduleChoice[];
    value: unknown;
    onChange: (v: unknown) => void;
}> = ({setting, choices, value, onChange}) => {
    const multi = setting.type === "string_list";
    if (!choices.length) {
        return (
            <span className="text-[11px] text-gray-500" title={setting.description ?? undefined}>
                {setting.title}: no choices yet
                {setting.source === "change_users" ? " (offered once a check has reported who changed what)" : ""}
            </span>
        );
    }
    if (multi) {
        const selected = new Set((Array.isArray(value) ? value : []).map((v) => selectedKey(v, choices)).filter(Boolean));
        return (
            <label className="flex flex-col text-[11px] text-gray-400" title={setting.description ?? undefined}>
                {setting.title}
                <select
                    multiple
                    className="bg-gray-800 border border-gray-700 rounded-sm px-1 py-0.5 text-xs text-gray-100 min-w-[10rem]"
                    size={Math.min(5, choices.length)}
                    value={[...selected]}
                    onChange={(e) => onChange([...e.target.selectedOptions].map((o) => valueOfKey(o.value, choices)))}
                >
                    {choices.map((c) => (
                        <option key={choiceKey(c.value)} value={choiceKey(c.value)}>
                            {c.label}
                        </option>
                    ))}
                </select>
            </label>
        );
    }
    return (
        <label className="flex flex-col text-[11px] text-gray-400" title={setting.description ?? undefined}>
            {setting.title}
            <select
                className="bg-gray-800 border border-gray-700 rounded-sm px-1 py-0.5 text-xs text-gray-100"
                value={selectedKey(value, choices)}
                onChange={(e) => onChange(valueOfKey(e.target.value, choices))}
            >
                <option value="">(provider default)</option>
                {choices.map((c) => (
                    <option key={choiceKey(c.value)} value={choiceKey(c.value)}>
                        {c.label}
                    </option>
                ))}
            </select>
        </label>
    );
};

/** Settings editor for one job and collection; fetches the choices that depend on them. */
const SettingsEditor: React.FC<{
    provider: ProviderSchedules;
    job: ProviderScheduleJob | undefined;
    collection: string;
    value: Record<string, unknown>;
    onChange: (v: Record<string, unknown>) => void;
}> = ({provider, job, collection, value, onChange}) => {
    const [choices, setChoices] = useState<Record<string, ScheduleChoice[]>>({});
    useEffect(() => {
        if (!job || !job.settings.length) return;
        let alive = true;
        void viewerApi
            .adminAssetScheduleChoices(provider.provider, job.id, collection)
            .then((c) => alive && setChoices(c))
            .catch(() => alive && setChoices({}));
        return () => {
            alive = false;
        };
    }, [provider.provider, job, collection]);
    if (!job || !job.settings.length) return null;
    return (
        <div className="flex flex-wrap gap-2">
            {job.settings.map((s) => (
                <SettingField
                    key={s.name}
                    setting={s}
                    choices={choices[s.name] ?? s.choices ?? []}
                    value={value[s.name]}
                    onChange={(v) => onChange({...value, [s.name]: v})}
                />
            ))}
        </div>
    );
};

const AddSchedule: React.FC<{
    provider: ProviderSchedules;
    frequencies: AssetSchedulesListing["frequencies"];
    onAdded: () => void;
    onSwept: () => void;
}> = ({provider, frequencies, onAdded, onSwept}) => {
    const [jobId, setJobId] = useState(provider.jobs[0]?.id ?? "");
    const collections = useMemo(() => sortProjects(provider.collections), [provider.collections]);
    const [collection, setCollection] = useState(collections[0] ?? "");
    const [frequency, setFrequency] = useState("daily");
    const [settings, setSettings] = useState<Record<string, unknown>>({});
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const job = provider.jobs.find((j) => j.id === jobId);
    const sweep = job?.kind === "sweep";

    const runOnce = async () => {
        setBusy(true);
        setError(null);
        try {
            await viewerApi.adminStartAssetSweep({provider: provider.provider, job: jobId, settings});
            onSwept();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    const add = async () => {
        setBusy(true);
        setError(null);
        try {
            await viewerApi.adminCreateAssetSchedule({
                provider: provider.provider,
                job: jobId,
                collection: sweep ? null : collection || null,
                frequency,
                settings,
            });
            setSettings({});
            onAdded();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    const select = "bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5 text-xs";
    return (
        <div className="rounded-sm border border-gray-800 bg-gray-900/40 p-2 space-y-1.5">
            <div className="flex flex-wrap items-end gap-2 text-xs">
                <select className={select} value={jobId} onChange={(e) => setJobId(e.target.value)} aria-label="Job">
                    {provider.jobs.map((j) => (
                        <option key={j.id} value={j.id}>
                            {j.label}
                        </option>
                    ))}
                </select>
                {sweep ? (
                    <span className="text-gray-400 py-0.5">every collection ({provider.collections.length})</span>
                ) : (
                    <select
                        className={select}
                        value={collection}
                        onChange={(e) => setCollection(e.target.value)}
                        aria-label="Collection"
                    >
                        {collections.map((c) => (
                            <option key={c} value={c}>
                                {c}
                            </option>
                        ))}
                    </select>
                )}
                <select
                    className={select}
                    value={frequency}
                    onChange={(e) => setFrequency(e.target.value)}
                    aria-label="Frequency"
                >
                    {frequencies.map((f) => (
                        <option key={f.id} value={f.id}>
                            {f.label}
                        </option>
                    ))}
                </select>
                <button
                    type="button"
                    className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-2 py-0.5 rounded-sm"
                    disabled={busy || !jobId || (!sweep && !!provider.collections.length && !collection)}
                    onClick={() => void add()}
                >
                    {busy ? "…" : "Add schedule"}
                </button>
                {sweep && (
                    <button
                        type="button"
                        className="border border-gray-700 hover:bg-gray-800 disabled:opacity-50 px-2 py-0.5 rounded-sm"
                        disabled={busy || !jobId}
                        onClick={() => void runOnce()}
                        title="Sweep every collection once, now, with these settings; no schedule is saved"
                    >
                        Run once now
                    </button>
                )}
            </div>
            {job?.description && <div className="text-[11px] text-gray-500">{job.description}</div>}
            <SettingsEditor
                provider={provider}
                job={job}
                collection={collection}
                value={settings}
                onChange={setSettings}
            />
            {error && <div className="text-xs text-red-300">{error}</div>}
        </div>
    );
};

const ScheduleRow: React.FC<{
    provider: ProviderSchedules;
    schedule: ProviderSchedule;
    frequencies: AssetSchedulesListing["frequencies"];
    onChanged: () => void;
}> = ({provider, schedule: s, frequencies, onChanged}) => {
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [editing, setEditing] = useState(false);
    const [settings, setSettings] = useState<Record<string, unknown>>(s.settings);
    const job = provider.jobs.find((j) => j.id === s.job);
    const last = lastRunText(s);

    const act = async (what: string, fn: () => Promise<unknown>) => {
        setBusy(what);
        setError(null);
        try {
            await fn();
            onChanged();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(null);
        }
    };

    return (
        <tr className="border-t border-gray-800 align-top">
            <td className="px-2 py-1.5">
                <div className="text-gray-100">{job?.label ?? s.job}</div>
                {!s.declared && <div className="text-[11px] text-amber-300">the provider no longer declares this job</div>}
            </td>
            <td className="px-2 py-1.5 font-mono">{s.kind === "sweep" ? "(every)" : (s.collection ?? "—")}</td>
            <td className="px-2 py-1.5">
                <select
                    className="bg-gray-800 border border-gray-700 rounded-sm px-1 py-0.5 text-xs"
                    value={s.frequency ?? ""}
                    disabled={busy !== null}
                    onChange={(e) => void act("freq", () => viewerApi.adminUpdateAssetSchedule(s.id, {frequency: e.target.value}))}
                    aria-label="Frequency"
                >
                    {!s.frequency && <option value="">custom</option>}
                    {frequencies.map((f) => (
                        <option key={f.id} value={f.id}>
                            {f.label}
                        </option>
                    ))}
                </select>
                <div className="text-[11px] text-gray-500 mt-0.5">next {fmt(s.next_fire_at)}</div>
            </td>
            <td className="px-2 py-1.5">
                {editing ? (
                    <div className="space-y-1">
                        <SettingsEditor
                            provider={provider}
                            job={job}
                            collection={s.collection ?? ""}
                            value={settings}
                            onChange={setSettings}
                        />
                        <div className="flex gap-1">
                            <button
                                type="button"
                                className="bg-blue-700 hover:bg-blue-600 text-white px-2 py-0.5 rounded-sm text-xs"
                                onClick={() =>
                                    void act("settings", async () => {
                                        await viewerApi.adminUpdateAssetSchedule(s.id, {settings});
                                        setEditing(false);
                                    })
                                }
                            >
                                Save
                            </button>
                            <button
                                type="button"
                                className="border border-gray-700 px-2 py-0.5 rounded-sm text-xs"
                                onClick={() => {
                                    setSettings(s.settings);
                                    setEditing(false);
                                }}
                            >
                                Cancel
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="flex items-start gap-1">
                        <span className="text-gray-300">{settingSummary(job, s.settings)}</span>
                        {!!job?.settings.length && (
                            <button
                                type="button"
                                className="text-[11px] text-blue-300 hover:text-blue-200"
                                onClick={() => setEditing(true)}
                            >
                                edit
                            </button>
                        )}
                    </div>
                )}
            </td>
            <td className={`px-2 py-1.5 ${last.tone}`}>{last.text}</td>
            <td className="px-2 py-1.5 whitespace-nowrap">
                <label className="inline-flex items-center gap-1 mr-2">
                    <input
                        type="checkbox"
                        checked={s.enabled}
                        disabled={busy !== null}
                        onChange={(e) =>
                            void act("enabled", () => viewerApi.adminUpdateAssetSchedule(s.id, {enabled: e.target.checked}))
                        }
                    />
                    on
                </label>
                <button
                    type="button"
                    className="border border-gray-700 hover:bg-gray-800 px-1.5 py-0.5 rounded-sm mr-1 disabled:opacity-50"
                    disabled={busy !== null}
                    onClick={() => void act("run", () => viewerApi.adminPluginJobScheduleRunNow(s.id))}
                    title="Run once now; the timetable is unchanged"
                >
                    {busy === "run" ? "…" : "Run now"}
                </button>
                <button
                    type="button"
                    className="bg-red-800 hover:bg-red-700 text-white px-1.5 py-0.5 rounded-sm disabled:opacity-50"
                    disabled={busy !== null}
                    onClick={() => {
                        if (confirm(`Remove the schedule "${job?.label ?? s.job}" ?`)) {
                            void act("remove", () => viewerApi.adminDeleteAssetSchedule(s.id));
                        }
                    }}
                >
                    Remove
                </button>
                {error && <div className="text-[11px] text-red-300 mt-1 whitespace-normal">{error}</div>}
            </td>
        </tr>
    );
};

const ProviderSchedulesSection: React.FC = () => {
    const [listing, setListing] = useState<AssetSchedulesListing | null>(null);
    const [error, setError] = useState<string | null>(null);
    // Bumped on every reload, so each provider's sweep list re-reads after a Run now / Run once.
    const [refreshKey, setRefreshKey] = useState(0);

    const load = useCallback(async () => {
        try {
            setListing(await viewerApi.adminAssetSchedules());
            setRefreshKey((k) => k + 1);
            setError(null);
        } catch (e) {
            setError(errText(e));
        }
    }, []);
    useEffect(() => {
        void load();
    }, [load]);

    const byProvider = useMemo(() => {
        const out = new Map<string, ProviderSchedule[]>();
        for (const s of listing?.schedules ?? []) out.set(s.provider, [...(out.get(s.provider) ?? []), s]);
        return out;
    }, [listing]);

    if (!listing) {
        return error ? <section className="px-3 py-3 text-xs text-red-300 border-b border-gray-800">{error}</section> : null;
    }
    const orphanProviders = [...byProvider.keys()].filter((p) => !listing.providers.some((x) => x.provider === p));

    return (
        <AdminSection
            title="Scheduled jobs"
            kind="Schedules"
            storageKey="ada.admin.providers.open.schedules"
            testId="provider-schedules-admin"
            actions={
                <button
                    type="button"
                    className="text-xs px-2 py-1 rounded-sm border border-gray-700 hover:bg-gray-800"
                    onClick={() => void load()}
                >
                    Refresh
                </button>
            }
        >
            <div className="px-3 pt-2 pb-1 space-y-1">
                <div className="text-xs text-gray-400">
                    Jobs a provider offers to run on a schedule, per collection. They always write to the provider store;
                    scopes read it through the grants under Access. A change check reports what
                    moved upstream; inspect a run in the Sources tab under Changes. Nothing is published automatically.
                </div>
            </div>
            {error && <div className="px-3 py-1 text-xs text-red-300">{error}</div>}
            {listing.providers.length === 0 && (
                <div className="px-3 py-2 text-xs text-gray-500">No online provider declares scheduled jobs.</div>
            )}
            {listing.providers.map((p) => (
                // Each provider's jobs set off from the next by an accent bar, not just spacing.
                <div key={p.provider} className="mx-3 my-2 space-y-1.5 border-l-2 border-blue-700/70 pl-3 py-1">
                    <div className="text-xs font-medium text-gray-100">
                        <code>{p.provider}</code>
                    </div>
                    {(byProvider.get(p.provider) ?? []).length > 0 && (
                        <div className="overflow-x-auto">
                            <table className="w-full text-xs">
                                <thead className="text-left text-gray-500">
                                    <tr>
                                        <th className="px-2 py-1 font-normal">Job</th>
                                        <th className="px-2 py-1 font-normal">Collection</th>
                                        <th className="px-2 py-1 font-normal">Frequency</th>
                                        <th className="px-2 py-1 font-normal">Settings</th>
                                        <th className="px-2 py-1 font-normal">Last result</th>
                                        <th className="px-2 py-1 font-normal" />
                                    </tr>
                                </thead>
                                <tbody>
                                    {(byProvider.get(p.provider) ?? []).map((s) => (
                                        <ScheduleRow
                                            key={s.id}
                                            provider={p}
                                            schedule={s}
                                            frequencies={listing.frequencies}
                                            onChanged={() => void load()}
                                        />
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                    <AddSchedule
                        provider={p}
                        frequencies={listing.frequencies}
                        onAdded={() => void load()}
                        onSwept={() => void load()}
                    />
                    {p.jobs.some((j) => j.kind === "sweep") && (
                        <>
                            <SweepDefaults provider={p.provider} />
                            <ProviderSweepRuns provider={p.provider} refreshKey={refreshKey} />
                        </>
                    )}
                </div>
            ))}
            {orphanProviders.length > 0 && (
                <div className="px-3 py-2 text-xs text-amber-300">
                    Schedules exist for {orphanProviders.join(", ")}, but no online worker advertises it right now; they
                    are skipped until it is back.
                </div>
            )}
            {listing.legacy.length > 0 && (
                <div className="px-3 py-2 space-y-1">
                    <div className="text-xs text-gray-300">Older plugin-job schedules</div>
                    <div className="text-[11px] text-gray-500">
                        Made before providers declared their schedules. They still run; recreate them above and remove
                        these.
                    </div>
                    <ul className="text-xs divide-y divide-gray-800">
                        {listing.legacy.map((l) => (
                            <li key={l.id} className="flex items-center gap-2 py-1">
                                <span className="flex-1 min-w-0 truncate" title={JSON.stringify(l.options)}>
                                    {l.name} — {l.plugin_id} · {l.scope} · <span className="font-mono">{l.cron_expr}</span>
                                    {!l.enabled && <span className="text-gray-500"> (off)</span>}
                                </span>
                                <button
                                    type="button"
                                    className="bg-red-800 hover:bg-red-700 text-white px-1.5 py-0.5 rounded-sm"
                                    onClick={() => {
                                        if (confirm(`Remove the older schedule "${l.name}"?`)) {
                                            void viewerApi
                                                .adminPluginJobScheduleArchive(l.id)
                                                .then(() => load())
                                                .catch((e) => setError(errText(e)));
                                        }
                                    }}
                                >
                                    Remove
                                </button>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </AdminSection>
    );
};

export default ProviderSchedulesSection;
