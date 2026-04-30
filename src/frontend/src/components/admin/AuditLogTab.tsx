import React, {useEffect, useState} from "react";
import {ApiError, AuditEntry, AuditFilters, viewerApi} from "@/services/viewerApi";

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
                        className="bg-gray-800 hover:bg-gray-700 px-2 py-1 rounded text-xs"
                        onClick={() => setFiltersOpen((v) => !v)}
                    >
                        Filters{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""} {filtersOpen ? "▲" : "▼"}
                    </button>
                    <button
                        className="ml-auto bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded text-xs"
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
                        className="hidden sm:inline-block ml-auto bg-blue-700 hover:bg-blue-600 px-2 py-1 rounded"
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
                        className="ml-auto bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded disabled:opacity-50"
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
                <table className="hidden sm:table w-full text-sm table-fixed min-w-[1200px]">
                    <colgroup>
                        <col className="w-[10rem]"/>
                        <col className="w-[8rem]"/>
                        <col className="w-[12rem]"/>
                        <col className="w-[6rem]"/>
                        <col className="min-w-[14rem]"/>
                        <col className="w-[6rem]"/>
                        <col className="w-[6rem]"/>
                    </colgroup>
                    <thead className="sticky top-0 bg-gray-800">
                    <tr className="text-left">
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
                                <span className="font-medium">{e.action}</span>
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
                    className="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded disabled:opacity-50"
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
            className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4"
            onClick={onClose}
        >
            <div
                className="bg-gray-900 border border-gray-700 rounded shadow-xl flex flex-col max-w-3xl w-full max-h-[85vh]"
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
                            className="shrink-0 bg-gray-800 hover:bg-gray-700 text-gray-100 px-2 py-1 rounded text-xs"
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
                    <div className="px-4 py-2 text-sm text-red-300 border-b border-gray-800 break-words">
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
            {entry.profile_key ? (
                <div className="pt-2 border-t border-gray-800">
                    <button
                        type="button"
                        className="bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded text-xs disabled:opacity-50"
                        onClick={onDownloadProfile}
                        disabled={downloading}
                    >
                        {downloading ? "Downloading…" : "Download profile (.prof)"}
                    </button>
                    <div className="text-[10px] text-gray-500 mt-1">
                        Loadable in snakeviz / speedscope / pstats.
                    </div>
                    {downloadErr && (
                        <div className="text-red-300 text-[10px] mt-1 break-all">{downloadErr}</div>
                    )}
                </div>
            ) : (
                <div className="text-[10px] text-gray-500 pt-2 border-t border-gray-800">
                    No profile attached. Toggle "Profile conversions" above and re-run.
                </div>
            )}
        </div>
    );
};

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
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-full sm:w-56 lg:w-72 text-white"
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
        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white w-full sm:w-auto"
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
