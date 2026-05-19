import React, {useEffect, useMemo, useState} from "react";
import {
    ApiError,
    AuditEntry,
    AuditFilters,
    MetricsSample,
    ProfileStatsRow,
    viewerApi,
} from "@/services/viewerApi";

// Filterable audit log view. Two layouts:
// * sm:↑ desktop — table with sticky header, fits everything in columns.
// * mobile — collapsible filters + card-per-entry, so a 320px viewport
//   stays readable without horizontal scrolling.
//
// Pagination is keyset on the BIGSERIAL id (the server returns
// next_before_id) — that way the table doesn't shift while new audit
// rows are inserted between pages.

const ACTIONS = ["", "upload", "download", "convert", "view"];
const KINDS = ["", "shared", "project", "user"];

const PROFILE_SETTING_KEY = "profile_conversions";

// Whether a row is a candidate for the (i) details modal. Convert
// rows always qualify (they may have metrics, traceback, or both);
// other rows only show the icon when they actually have an error or
// traceback to surface, so the column doesn't fill with no-op
// buttons.
function hasDetails(e: AuditEntry): boolean {
    if (e.action === "convert") return true;
    return Boolean(e.error || e.traceback);
}

function hasMetrics(e: AuditEntry): boolean {
    return (
        e.cpu_user_ms != null ||
        e.cpu_sys_ms != null ||
        e.peak_rss_kb != null ||
        e.read_bytes != null ||
        e.write_bytes != null ||
        e.profile_key != null ||
        e.duration_ms != null
    );
}

const AuditLogTab: React.FC = () => {
    const [filters, setFilters] = useState<AuditFilters>({limit: 100});
    const [entries, setEntries] = useState<AuditEntry[]>([]);
    const [nextBeforeId, setNextBeforeId] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [detailsEntry, setDetailsEntry] = useState<AuditEntry | null>(null);
    const [profileEnabled, setProfileEnabled] = useState(false);
    const [profileSaving, setProfileSaving] = useState(false);
    const [clearing, setClearing] = useState(false);
    const activeFilterCount = countActive(filters);

    // Initial fetch of the profile-conversions toggle. Failures are
    // non-fatal — the row still renders, the toggle just stays off.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const v = await viewerApi.adminGetSetting(PROFILE_SETTING_KEY);
                if (!cancelled) setProfileEnabled((v || "").toLowerCase() === "true");
            } catch {
                /* ignore */
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const onProfileToggle = async (next: boolean) => {
        setProfileSaving(true);
        try {
            await viewerApi.adminSetSetting(PROFILE_SETTING_KEY, next ? "true" : "false");
            setProfileEnabled(next);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setProfileSaving(false);
        }
    };

    const onClearMetrics = async () => {
        if (!window.confirm(
            "Clear all conversion metrics and delete profile blobs? Audit rows themselves stay; only the metrics columns are nulled."
        )) return;
        setClearing(true);
        try {
            const r = await viewerApi.adminClearMetrics();
            await reload(filters);
            window.alert(
                `Cleared ${r.rows_cleared} row(s); deleted ${r.profiles_deleted} profile blob(s).` +
                (r.errors.length ? `\n${r.errors.length} error(s) — see browser console.` : "")
            );
            if (r.errors.length) console.warn("clear metrics errors", r.errors);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setClearing(false);
        }
    };

    const reload = async (f: AuditFilters) => {
        setLoading(true);
        setError(null);
        try {
            const r = await viewerApi.adminAudit({...f, before_id: undefined});
            setEntries(r.entries);
            setNextBeforeId(r.next_before_id);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setLoading(false);
        }
    };

    const loadMore = async () => {
        if (nextBeforeId == null) return;
        setLoading(true);
        try {
            const r = await viewerApi.adminAudit({...filters, before_id: nextBeforeId});
            setEntries((prev) => [...prev, ...r.entries]);
            setNextBeforeId(r.next_before_id);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void reload(filters);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const onFilter = (next: Partial<AuditFilters>) => {
        const merged = {...filters, ...next};
        setFilters(merged);
        void reload(merged);
    };

    return (
        <div className="flex flex-col h-full">
            <div className="border-b border-gray-700">
                <div className="flex items-center gap-2 px-3 py-2 sm:hidden">
                    <button
                        className="bg-gray-800 hover:bg-gray-700 px-2 py-1 rounded-sm text-xs"
                        onClick={() => setFiltersOpen((v) => !v)}
                    >
                        Filters{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""} {filtersOpen ? "▲" : "▼"}
                    </button>
                    <button
                        className="ml-auto bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded-sm text-xs"
                        onClick={() => reload(filters)}
                        disabled={loading}
                    >
                        {loading ? "Loading…" : "Refresh"}
                    </button>
                </div>
                <div
                    className={
                        (filtersOpen ? "flex" : "hidden") +
                        " sm:flex flex-wrap gap-2 px-3 sm:px-4 pb-2 sm:py-2 text-xs"
                    }
                >
                    <FilterInput
                        placeholder="user_sub"
                        value={filters.user_sub || ""}
                        onChange={(v) => onFilter({user_sub: v || undefined})}
                    />
                    <FilterSelect
                        options={KINDS}
                        value={filters.scope_kind || ""}
                        onChange={(v) => onFilter({scope_kind: v || undefined})}
                        placeholder="any kind"
                    />
                    <FilterInput
                        placeholder="scope_id"
                        value={filters.scope_id || ""}
                        onChange={(v) => onFilter({scope_id: v || undefined})}
                    />
                    <FilterSelect
                        options={ACTIONS}
                        value={filters.action || ""}
                        onChange={(v) => onFilter({action: v || undefined})}
                        placeholder="any action"
                    />
                    <button
                        className="hidden sm:inline-block ml-auto bg-blue-700 hover:bg-blue-600 px-2 py-1 rounded-sm"
                        onClick={() => reload(filters)}
                        disabled={loading}
                    >
                        Refresh
                    </button>
                </div>
                {/* Per-deployment knobs that affect future runs.
                    Profile toggle persists in app_settings; Clear
                    metrics nulls out columns + deletes blobs.
                    Visually separated so the controls are obvious on
                    mobile (where they otherwise sit flush with the
                    Filters / Refresh row). */}
                <div className="flex flex-wrap items-center gap-3 px-3 sm:px-4 py-2 text-xs border-t border-gray-800 bg-gray-900/40">
                    <span className="font-semibold text-gray-300 uppercase tracking-wide text-[10px]">
                        Metrics
                    </span>
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={profileEnabled}
                            onChange={(e) => onProfileToggle(e.target.checked)}
                            disabled={profileSaving}
                            className="h-4 w-4"
                        />
                        <span>
                            Profile conversions
                            {profileSaving ? <span className="text-gray-400"> (saving…)</span> : null}
                        </span>
                    </label>
                    <button
                        className="ml-auto bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded-sm disabled:opacity-50"
                        onClick={onClearMetrics}
                        disabled={clearing}
                        title="Null out all metrics columns and delete profile blobs"
                    >
                        {clearing ? "Clearing…" : "Clear metrics"}
                    </button>
                </div>
            </div>
            {error && (
                <div className="px-3 sm:px-4 py-2 text-red-300 text-xs border-b border-gray-700">
                    {error}
                </div>
            )}
            <div className="flex-1 overflow-auto">
                {/* Desktop / tablet table.
                    Time/User/Scope/Action/Target/Status are predictable in
                    width, so we fix them and let Key flex to consume the
                    rest of the row. Removes the postage-stamp truncation
                    that kicked in even when the modal had room to show
                    the full path. */}
                <table className="hidden sm:table w-full text-sm table-fixed min-w-[1260px]">
                    <colgroup>
                        <col className="w-16"/>
                        <col className="w-40"/>
                        <col className="w-32"/>
                        <col className="w-48"/>
                        <col className="w-24"/>
                        <col className="min-w-56"/>
                        <col className="w-24"/>
                        <col className="w-24"/>
                    </colgroup>
                    <thead className="sticky top-0 bg-gray-800">
                    <tr className="text-left">
                        <Th>ID</Th>
                        <Th>Time</Th>
                        <Th>User</Th>
                        <Th>Scope</Th>
                        <Th>Action</Th>
                        <Th>Key</Th>
                        <Th>Target</Th>
                        <Th>Status</Th>
                    </tr>
                    </thead>
                    <tbody>
                    {entries.map((e) => (
                        <tr key={e.id} className="border-t border-gray-800 hover:bg-gray-800/40">
                            <Td>
                                <span className="font-mono text-gray-300">#{e.id}</span>
                            </Td>
                            <Td title={e.ts || ""}>{formatTs(e.ts)}</Td>
                            <Td title={e.user_sub || ""}>{shortSub(e.user_sub)}</Td>
                            <Td title={e.scope_id || ""}>
                                {e.scope_kind}
                                {e.scope_id ? `:${shortSub(e.scope_id)}` : ""}
                            </Td>
                            <Td>{e.action}</Td>
                            <Td title={e.key || ""}>{e.key || ""}</Td>
                            <Td>{e.target_format || ""}</Td>
                            <Td title={e.error || ""}>
                                <span className={statusClass(e.status)}>{e.status || ""}</span>
                                {hasDetails(e) && (
                                    <button
                                        type="button"
                                        className="ml-1 inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-500 text-gray-300 hover:text-white hover:border-white text-[10px] font-bold leading-none align-middle no-drag"
                                        onClick={() => setDetailsEntry(e)}
                                        title={e.error ? "Show error / metrics" : "Show metrics"}
                                        aria-label="Show details"
                                    >
                                        i
                                    </button>
                                )}
                            </Td>
                        </tr>
                    ))}
                    </tbody>
                </table>
                {/* Mobile cards */}
                <ul className="sm:hidden divide-y divide-gray-800">
                    {entries.map((e) => (
                        <li key={e.id} className="px-3 py-2 text-xs">
                            <div className="flex items-baseline justify-between gap-2">
                                <span className="font-medium">
                                    <span className="font-mono text-gray-400 mr-1">#{e.id}</span>
                                    {e.action}
                                </span>
                                <span className={statusClass(e.status) + " text-[11px]"}>
                                    {e.status || ""}
                                </span>
                            </div>
                            <div className="text-gray-400 mt-0.5">
                                {formatTs(e.ts)} · {e.scope_kind}
                                {e.scope_id ? `:${shortSub(e.scope_id)}` : ""}
                            </div>
                            {e.key && (
                                <div className="text-gray-300 mt-1 break-all" title={e.key}>
                                    {e.key}
                                    {e.target_format ? (
                                        <span className="text-gray-400"> → {e.target_format}</span>
                                    ) : null}
                                </div>
                            )}
                            {e.user_sub && (
                                <div className="text-gray-500 mt-0.5" title={e.user_sub}>
                                    by {shortSub(e.user_sub)}
                                </div>
                            )}
                            {e.error && (
                                <div className="text-red-300 mt-1 break-all flex items-start gap-1" title={e.error}>
                                    <span className="flex-1">{e.error}</span>
                                    <button
                                        type="button"
                                        className="shrink-0 inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-500 text-gray-300 hover:text-white hover:border-white text-[10px] font-bold leading-none mt-0.5 no-drag"
                                        onClick={() => setDetailsEntry(e)}
                                        aria-label="Show details"
                                    >
                                        i
                                    </button>
                                </div>
                            )}
                            {!e.error && hasDetails(e) && (
                                <div className="mt-1">
                                    <button
                                        type="button"
                                        className="text-[10px] text-gray-400 hover:text-white underline no-drag"
                                        onClick={() => setDetailsEntry(e)}
                                    >
                                        details
                                    </button>
                                </div>
                            )}
                        </li>
                    ))}
                </ul>
                {!loading && entries.length === 0 && (
                    <div className="px-4 py-8 text-center text-gray-500 text-sm">
                        No matching audit entries.
                    </div>
                )}
            </div>
            <div className="border-t border-gray-700 px-3 sm:px-4 py-2 flex items-center gap-3 text-xs">
                <span className="text-gray-400">{entries.length} rows</span>
                <button
                    className="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded-sm disabled:opacity-50"
                    onClick={loadMore}
                    disabled={loading || nextBeforeId == null}
                >
                    Load more
                </button>
                {loading && <span className="text-gray-500">loading…</span>}
            </div>
            {detailsEntry && (
                <DetailsModal entry={detailsEntry} onClose={() => setDetailsEntry(null)}/>
            )}
        </div>
    );
};

// Tabbed details view: Error (or 'OK' summary) on one tab, Metrics
// on the other. Both tabs render even when their data is partial so
// the user gets a consistent layout regardless of job outcome — a
// timed-out conversion and a clean success share the same shape.
const DetailsModal: React.FC<{entry: AuditEntry; onClose: () => void}> = ({entry, onClose}) => {
    const [tab, setTab] = useState<"error" | "metrics">(
        entry.error || entry.traceback ? "error" : "metrics",
    );
    const [copied, setCopied] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const [downloadErr, setDownloadErr] = useState<string | null>(null);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    const traceText = entry.traceback || entry.error || "";
    const copyPayload =
        (entry.error ? `${entry.error}\n\n` : "") + (entry.traceback || "");

    const onCopy = async () => {
        try {
            await navigator.clipboard.writeText(copyPayload || traceText);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — user can still select-and-copy by hand */
        }
    };

    const onDownloadProfile = async () => {
        if (!entry.profile_key) return;
        setDownloading(true);
        setDownloadErr(null);
        try {
            // Suggest the storage filename so the user gets a stable
            // name on disk; tail of the key after the last slash.
            const suggested = entry.profile_key.split("/").pop() || `audit-${entry.id}.prof`;
            await viewerApi.adminDownloadProfile(entry.id, suggested);
        } catch (e) {
            setDownloadErr(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setDownloading(false);
        }
    };

    return (
        <div
            className="fixed inset-0 z-60 flex items-start sm:items-center justify-center bg-black/70 p-4 overflow-y-auto"
            onClick={onClose}
        >
            <div
                className="bg-gray-900 border border-gray-700 rounded-sm shadow-xl flex flex-col max-w-3xl w-full max-h-[calc(100dvh-2rem)] sm:max-h-[85dvh] my-auto"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-label="Audit row details"
            >
                <div className="flex items-start gap-3 border-b border-gray-700 px-4 py-2">
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold">Audit details</div>
                        <div className="text-xs text-gray-400 truncate" title={entry.key || ""}>
                            {entry.action}
                            {entry.key ? ` · ${entry.key}` : ""}
                            {entry.target_format ? ` → ${entry.target_format}` : ""}
                        </div>
                    </div>
                    {tab === "error" && entry.traceback && (
                        <button
                            type="button"
                            className="shrink-0 bg-gray-800 hover:bg-gray-700 text-gray-100 px-2 py-1 rounded-sm text-xs"
                            onClick={onCopy}
                            title="Copy traceback to clipboard"
                        >
                            {copied ? "Copied" : "Copy"}
                        </button>
                    )}
                    <button
                        type="button"
                        className="shrink-0 text-gray-300 hover:text-white text-xl leading-none px-2"
                        onClick={onClose}
                        aria-label="Close"
                        title="Close (Esc)"
                    >
                        ×
                    </button>
                </div>
                <div className="flex border-b border-gray-700 text-xs">
                    <TabButton active={tab === "error"} onClick={() => setTab("error")}>
                        {entry.error ? "Error" : "Outcome"}
                    </TabButton>
                    <TabButton active={tab === "metrics"} onClick={() => setTab("metrics")}>
                        Metrics
                        {hasMetrics(entry) ? null : (
                            <span className="text-gray-500"> (none)</span>
                        )}
                    </TabButton>
                </div>
                <div className="flex-1 overflow-auto">
                    {tab === "error" && (
                        <ErrorTab entry={entry}/>
                    )}
                    {tab === "metrics" && (
                        <MetricsTab
                            entry={entry}
                            onDownloadProfile={onDownloadProfile}
                            downloading={downloading}
                            downloadErr={downloadErr}
                        />
                    )}
                </div>
            </div>
        </div>
    );
};

const TabButton: React.FC<{active: boolean; onClick: () => void; children: React.ReactNode}> = ({
    active, onClick, children,
}) => (
    <button
        type="button"
        onClick={onClick}
        className={
            "px-3 py-1.5 border-b-2 " +
            (active
                ? "border-blue-500 text-white"
                : "border-transparent text-gray-400 hover:text-gray-200")
        }
    >
        {children}
    </button>
);

const ErrorTab: React.FC<{entry: AuditEntry}> = ({entry}) => {
    if (entry.error || entry.traceback) {
        return (
            <>
                {entry.error && (
                    <div className="px-4 py-2 text-sm text-red-300 border-b border-gray-800 wrap-break-word">
                        {entry.error}
                    </div>
                )}
                {entry.traceback ? (
                    <pre className="px-4 py-2 text-xs text-gray-200 whitespace-pre font-mono">
{entry.traceback}
                    </pre>
                ) : (
                    <div className="px-4 py-3 text-xs text-gray-400">
                        No traceback recorded for this entry.
                    </div>
                )}
            </>
        );
    }
    return (
        <div className="px-4 py-3 text-xs text-gray-300 space-y-1">
            <div>Status: <span className="font-mono">{entry.status || "n/a"}</span></div>
            {entry.duration_ms != null && (
                <div>Duration: <span className="font-mono">{formatDuration(entry.duration_ms)}</span></div>
            )}
            {entry.job_id && (
                <div className="break-all">Job: <span className="font-mono">{entry.job_id}</span></div>
            )}
            <div className="text-gray-500 mt-2">
                No error reported for this entry. Switch to the Metrics tab for
                CPU / memory / IO data.
            </div>
        </div>
    );
};

const MetricsTab: React.FC<{
    entry: AuditEntry;
    onDownloadProfile: () => void;
    downloading: boolean;
    downloadErr: string | null;
}> = ({entry, onDownloadProfile, downloading, downloadErr}) => {
    if (!hasMetrics(entry)) {
        return (
            <div className="px-4 py-3 text-xs text-gray-400">
                No metrics captured for this entry.
                {entry.action !== "convert" ? (
                    <span> Only conversion runs collect resource metrics.</span>
                ) : (
                    <span> The worker that processed this job pre-dates the metrics column.</span>
                )}
            </div>
        );
    }
    return (
        <div className="px-4 py-3 text-xs text-gray-200 space-y-3">
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 font-mono">
                <MetricRow label="Wall" value={formatDuration(entry.duration_ms)}/>
                <MetricRow label="CPU user" value={formatDuration(entry.cpu_user_ms)}/>
                <MetricRow label="CPU sys"  value={formatDuration(entry.cpu_sys_ms)}/>
                <MetricRow label="Peak RSS" value={formatBytes((entry.peak_rss_kb ?? null) != null ? (entry.peak_rss_kb as number) * 1024 : null)}/>
                <MetricRow label="Read"     value={formatBytes(entry.read_bytes)}/>
                <MetricRow label="Write"    value={formatBytes(entry.write_bytes)}/>
            </dl>
            <MetricsHistoryChart auditId={entry.id}/>
            {entry.profile_key ? (
                <div className="pt-2 border-t border-gray-800 space-y-3">
                    <div>
                        <button
                            type="button"
                            className="bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded-sm text-xs disabled:opacity-50"
                            onClick={onDownloadProfile}
                            disabled={downloading}
                        >
                            {downloading ? "Downloading…" : "Download profile (.prof)"}
                        </button>
                        <span className="text-[10px] text-gray-500 ml-2">
                            Loadable in snakeviz / speedscope / pstats.
                        </span>
                        {downloadErr && (
                            <div className="text-red-300 text-[10px] mt-1 break-all">{downloadErr}</div>
                        )}
                    </div>
                    {/* Inline per-function stats — sortable and searchable so
                        the operator can find hot frames without leaving the
                        UI. Server-side pstats parse keeps the SPA bundle
                        free of marshal/pickle parsers. */}
                    <ProfileStatsTable auditId={entry.id} totalWallMs={entry.duration_ms}/>
                </div>
            ) : (
                <div className="text-[10px] text-gray-500 pt-2 border-t border-gray-800">
                    No profile attached. Toggle "Profile conversions" above and re-run.
                </div>
            )}
        </div>
    );
};

const MetricsHistoryChart: React.FC<{auditId: number}> = ({auditId}) => {
    const [samples, setSamples] = useState<MetricsSample[] | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setErr(null);
        viewerApi.adminMetricsHistory(auditId)
            .then((r) => {
                if (!cancelled) setSamples(r.samples);
            })
            .catch((e) => {
                if (!cancelled) setErr(e instanceof ApiError ? e.detail || e.message : String(e));
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [auditId]);

    if (loading) {
        return <div className="text-[10px] text-gray-400">Loading resource timeline…</div>;
    }
    if (err) {
        return <div className="text-[10px] text-red-300 break-all">timeline: {err}</div>;
    }
    if (!samples || samples.length === 0) {
        return (
            <div className="text-[10px] text-gray-500 pt-1 border-t border-gray-800">
                No per-heartbeat samples for this run. Older jobs predate the
                subprocess wrapper, or the worker died before any sample landed.
            </div>
        );
    }

    const maxElapsed = Math.max(...samples.map((s) => s.elapsed_s), 1);
    const maxRss = Math.max(...samples.map((s) => Math.max(s.rss_kb, s.peak_rss_kb)), 1);
    const maxCpuMs = Math.max(...samples.map((s) => s.cpu_user_ms + s.cpu_sys_ms), 1);
    const maxIo = Math.max(...samples.map((s) => Math.max(s.read_bytes, s.write_bytes)), 1);

    return (
        <div className="space-y-2 pt-2 border-t border-gray-800">
            <div className="text-[10px] uppercase tracking-wide text-gray-500">
                Resource timeline ({samples.length} samples · {maxElapsed.toFixed(0)}s)
            </div>
            <ChartPanel
                title="RSS"
                yLabel={formatBytes(maxRss * 1024)}
                series={[
                    {color: "#60a5fa", points: samples.map((s) => [s.elapsed_s / maxElapsed, s.rss_kb / maxRss]), label: "rss"},
                    {color: "#f87171", points: samples.map((s) => [s.elapsed_s / maxElapsed, s.peak_rss_kb / maxRss]), label: "peak"},
                ]}
            />
            <ChartPanel
                title="CPU (user+sys)"
                yLabel={formatDuration(maxCpuMs)}
                series={[
                    {
                        color: "#34d399",
                        points: samples.map((s) => [s.elapsed_s / maxElapsed, (s.cpu_user_ms + s.cpu_sys_ms) / maxCpuMs]),
                        label: "cpu",
                    },
                ]}
            />
            <ChartPanel
                title="IO bytes"
                yLabel={formatBytes(maxIo)}
                series={[
                    {color: "#a78bfa", points: samples.map((s) => [s.elapsed_s / maxElapsed, s.read_bytes / maxIo]), label: "read"},
                    {color: "#fbbf24", points: samples.map((s) => [s.elapsed_s / maxElapsed, s.write_bytes / maxIo]), label: "write"},
                ]}
            />
        </div>
    );
};

const ChartPanel: React.FC<{
    title: string;
    yLabel: string;
    series: {color: string; points: [number, number][]; label: string}[];
}> = ({title, yLabel, series}) => {
    const W = 320;
    const H = 56;
    return (
        <div>
            <div className="flex items-baseline justify-between gap-2 mb-0.5">
                <div className="text-[10px] text-gray-400 font-mono">{title}</div>
                <div className="text-[10px] text-gray-500 font-mono">max ≈ {yLabel}</div>
            </div>
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-14 bg-gray-900/60 border border-gray-800 rounded-sm">
                {series.map((s, i) => {
                    if (s.points.length === 0) return null;
                    const d = s.points
                        .map(([x, y], idx) =>
                            `${idx === 0 ? "M" : "L"}${(x * W).toFixed(2)},${(H - y * H).toFixed(2)}`,
                        )
                        .join(" ");
                    return (
                        <g key={i}>
                            <path d={d} stroke={s.color} strokeWidth={1.2} fill="none"/>
                        </g>
                    );
                })}
            </svg>
            <div className="flex gap-3 mt-0.5 text-[10px] text-gray-500 font-mono">
                {series.map((s, i) => (
                    <span key={i} style={{color: s.color}}>● <span className="text-gray-400">{s.label}</span></span>
                ))}
            </div>
        </div>
    );
};


type StatsSortKey = "func" | "ncalls" | "primitive_calls" | "tottime" | "percall_tot" | "cumtime" | "percall_cum";

const ProfileStatsTable: React.FC<{auditId: number; totalWallMs: number | null}> = ({auditId, totalWallMs}) => {
    const [resp, setResp] = useState<{rows: ProfileStatsRow[]; total_tottime: number} | null>(null);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [filter, setFilter] = useState("");
    const [sortKey, setSortKey] = useState<StatsSortKey>("cumtime");
    const [sortDesc, setSortDesc] = useState(true);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setErr(null);
        viewerApi.adminProfileStats(auditId, 1000)
            .then((r) => {
                if (cancelled) return;
                setResp({rows: r.rows, total_tottime: r.total_tottime});
            })
            .catch((e) => {
                if (cancelled) return;
                setErr(e instanceof ApiError ? e.detail || e.message : String(e));
            })
            .finally(() => {
                if (cancelled) return;
                setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [auditId]);

    const filtered = useMemo(() => {
        if (!resp) return [];
        const q = filter.trim().toLowerCase();
        const rows = q
            ? resp.rows.filter(
                  (r) =>
                      r.func.toLowerCase().includes(q) ||
                      r.file.toLowerCase().includes(q),
              )
            : resp.rows;
        const sorted = [...rows];
        sorted.sort((a, b) => {
            const av = a[sortKey];
            const bv = b[sortKey];
            if (typeof av === "number" && typeof bv === "number") return sortDesc ? bv - av : av - bv;
            return sortDesc ? String(bv).localeCompare(String(av)) : String(av).localeCompare(String(bv));
        });
        return sorted;
    }, [resp, filter, sortKey, sortDesc]);

    if (loading) {
        return (
            <div className="text-[10px] text-gray-400">Loading profile stats…</div>
        );
    }
    if (err) {
        return (
            <div className="text-[10px] text-red-300 break-all">profile stats: {err}</div>
        );
    }
    if (!resp || resp.rows.length === 0) {
        return <div className="text-[10px] text-gray-400">No frames recorded.</div>;
    }

    const onHeader = (k: StatsSortKey) => {
        if (k === sortKey) {
            setSortDesc((v) => !v);
        } else {
            setSortKey(k);
            setSortDesc(k !== "func");
        }
    };

    // Top-N visual: a single horizontal bar per function (cumtime as % of
    // total tottime) gives a "where did the time go" cue alongside the
    // table — same Pareto graph snakeviz draws, just inline.
    const topByCum = [...resp.rows].sort((a, b) => b.cumtime - a.cumtime).slice(0, 10);
    const maxCum = topByCum.length > 0 ? topByCum[0].cumtime : 1;

    return (
        <div className="text-[11px] space-y-2">
            <div className="text-gray-400">
                <span className="font-mono">{resp.rows.length}</span> functions ·{" "}
                <span className="font-mono">{resp.total_tottime.toFixed(2)}s</span> total self-time
                {totalWallMs != null ? (
                    <span> · wall <span className="font-mono">{(totalWallMs / 1000).toFixed(2)}s</span></span>
                ) : null}
            </div>
            {/* Top-10 cumulative-time bar chart */}
            <div className="space-y-0.5 bg-gray-900/60 border border-gray-800 rounded-sm p-2">
                <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                    Top 10 by cumulative time
                </div>
                {topByCum.map((r, i) => (
                    <div key={`${r.file}:${r.line}:${r.func}:${i}`} className="flex items-center gap-2">
                        <div className="flex-1 min-w-0">
                            <div className="font-mono text-gray-200 truncate" title={`${r.file}:${r.line}`}>
                                {r.func}
                            </div>
                            <div className="h-1 bg-gray-800 rounded-sm overflow-hidden">
                                <div
                                    className="h-full bg-blue-500"
                                    style={{width: `${maxCum > 0 ? (r.cumtime / maxCum) * 100 : 0}%`}}
                                />
                            </div>
                        </div>
                        <div className="font-mono text-gray-300 w-16 text-right shrink-0">
                            {r.cumtime.toFixed(3)}s
                        </div>
                    </div>
                ))}
            </div>
            <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter by function or file…"
                className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 w-full text-white"
            />
            <div className="overflow-auto max-h-96 border border-gray-800 rounded-sm">
                <table className="w-full font-mono">
                    <thead className="sticky top-0 bg-gray-800 text-gray-300">
                        <tr className="text-right">
                            <StatsTh active={sortKey === "func"} desc={sortDesc} onClick={() => onHeader("func")} align="left">function</StatsTh>
                            <StatsTh active={sortKey === "ncalls"} desc={sortDesc} onClick={() => onHeader("ncalls")}>ncalls</StatsTh>
                            <StatsTh active={sortKey === "primitive_calls"} desc={sortDesc} onClick={() => onHeader("primitive_calls")}>prim</StatsTh>
                            <StatsTh active={sortKey === "tottime"} desc={sortDesc} onClick={() => onHeader("tottime")}>tottime</StatsTh>
                            <StatsTh active={sortKey === "percall_tot"} desc={sortDesc} onClick={() => onHeader("percall_tot")}>percall</StatsTh>
                            <StatsTh active={sortKey === "cumtime"} desc={sortDesc} onClick={() => onHeader("cumtime")}>cumtime</StatsTh>
                            <StatsTh active={sortKey === "percall_cum"} desc={sortDesc} onClick={() => onHeader("percall_cum")}>percum</StatsTh>
                        </tr>
                    </thead>
                    <tbody>
                        {filtered.map((r, i) => (
                            <tr key={`${r.file}:${r.line}:${r.func}:${i}`} className="border-t border-gray-800 text-right">
                                <td className="px-2 py-0.5 text-left text-gray-200 truncate max-w-[20rem]" title={`${r.file}:${r.line}`}>
                                    <span className="text-gray-500">{shortFile(r.file)}:{r.line} </span>
                                    {r.func}
                                </td>
                                <td className="px-2 py-0.5 text-gray-300">{r.ncalls.toLocaleString()}</td>
                                <td className="px-2 py-0.5 text-gray-400">{r.primitive_calls.toLocaleString()}</td>
                                <td className="px-2 py-0.5 text-gray-200">{r.tottime.toFixed(3)}</td>
                                <td className="px-2 py-0.5 text-gray-400">{formatPercall(r.percall_tot)}</td>
                                <td className="px-2 py-0.5 text-gray-200">{r.cumtime.toFixed(3)}</td>
                                <td className="px-2 py-0.5 text-gray-400">{formatPercall(r.percall_cum)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {filtered.length === 0 && (
                    <div className="text-center text-gray-500 py-3">No frames match the filter.</div>
                )}
            </div>
        </div>
    );
};

const StatsTh: React.FC<{
    active: boolean;
    desc: boolean;
    onClick: () => void;
    align?: "left" | "right";
    children: React.ReactNode;
}> = ({active, desc, onClick, align = "right", children}) => (
    <th
        className={
            "px-2 py-1 select-none cursor-pointer hover:text-white whitespace-nowrap " +
            (align === "left" ? "text-left" : "text-right")
        }
        onClick={onClick}
    >
        {children}
        {active ? <span className="text-blue-400">{desc ? " ▾" : " ▴"}</span> : null}
    </th>
);

function shortFile(p: string): string {
    if (!p) return "";
    const parts = p.split("/");
    if (parts.length <= 2) return p;
    return ".../" + parts.slice(-2).join("/");
}

function formatPercall(v: number): string {
    if (!isFinite(v) || v === 0) return "0";
    if (v >= 1) return v.toFixed(3);
    if (v >= 1e-3) return (v * 1000).toFixed(2) + "ms";
    return (v * 1e6).toFixed(1) + "µs";
}

const MetricRow: React.FC<{label: string; value: string}> = ({label, value}) => (
    <>
        <dt className="text-gray-400">{label}</dt>
        <dd>{value}</dd>
    </>
);

function formatDuration(ms: number | null): string {
    if (ms == null) return "–";
    if (ms < 1000) return `${ms} ms`;
    const s = ms / 1000;
    if (s < 60) return `${s.toFixed(2)} s`;
    const m = Math.floor(s / 60);
    const rem = (s - m * 60).toFixed(1);
    return `${m}m ${rem}s`;
}

function formatBytes(n: number | null): string {
    if (n == null) return "–";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MiB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GiB`;
}

const FilterInput: React.FC<{
    placeholder: string;
    value: string;
    onChange: (v: string) => void;
}> = ({placeholder, value, onChange}) => {
    const [local, setLocal] = useState(value);
    useEffect(() => setLocal(value), [value]);
    return (
        <input
            className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 w-full sm:w-56 lg:w-72 text-white"
            placeholder={placeholder}
            value={local}
            onChange={(e) => setLocal(e.target.value)}
            onBlur={() => onChange(local.trim())}
            onKeyDown={(e) => {
                if (e.key === "Enter") onChange(local.trim());
            }}
        />
    );
};

const FilterSelect: React.FC<{
    options: string[];
    value: string;
    onChange: (v: string) => void;
    placeholder: string;
}> = ({options, value, onChange, placeholder}) => (
    <select
        className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-white w-full sm:w-auto"
        value={value}
        onChange={(e) => onChange(e.target.value)}
    >
        {options.map((o) =>
            o === "" ? (
                <option key="" value="">
                    {placeholder}
                </option>
            ) : (
                <option key={o} value={o}>
                    {o}
                </option>
            ),
        )}
    </select>
);

const Th: React.FC<{children: React.ReactNode}> = ({children}) => (
    <th className="px-3 py-2 font-medium text-gray-300 whitespace-nowrap">{children}</th>
);

// Truncation lives at the cell level so long values (paths, error
// messages, full subs) don't break layout — but we let the column
// widths do the gating now via <colgroup>, not a hard 20ch cap.
const Td: React.FC<{children: React.ReactNode; title?: string}> = ({children, title}) => (
    <td className="px-3 py-1 truncate" title={title}>
        {children}
    </td>
);

function shortSub(s: string | null): string {
    if (!s) return "";
    if (s.length <= 12) return s;
    return `${s.slice(0, 8)}…${s.slice(-4)}`;
}

function formatTs(ts: string | null): string {
    if (!ts) return "";
    return ts.replace("T", " ").slice(0, 19);
}

function statusClass(s: string | null): string {
    if (s === "ok" || s === "done") return "text-green-400";
    if (s === "error") return "text-red-400";
    if (s === "queued") return "text-yellow-300";
    return "text-gray-300";
}

function countActive(f: AuditFilters): number {
    let n = 0;
    if (f.user_sub) n++;
    if (f.scope_kind) n++;
    if (f.scope_id) n++;
    if (f.action) n++;
    return n;
}

export default AuditLogTab;
