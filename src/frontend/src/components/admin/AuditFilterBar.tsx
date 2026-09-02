import React, {useEffect, useState} from "react";

import type {AuditFilters} from "@/services/viewerApi";
import {
    AUDIT_FILTER_KEYS,
    AUDIT_RANGES,
    countActiveAuditFilters,
    useAuditFilterStore,
} from "@/state/auditFilterStore";

// The Audit tab's filter bar.
//
// Lifted out of AuditLogTab so one filter can drive Overview, Log and Runs.
// It renders ABOVE the sub-tab strip on purpose: a filter that sits inside a
// sub-tab reads as belonging to it, and the point of the consolidation is that
// narrowing on Overview survives the click into Log.
//
// COLLAPSIBLE AT EVERY WIDTH, not just mobile. The tab now stacks an admin tab
// strip, a filter bar and a sub-tab strip above the content; seven controls
// pinned open cost more vertical room than the thing being filtered is worth
// once you have set them. Collapsed, the bar keeps a chip per active filter —
// a collapsed bar that hid an active filter would leave the operator reading a
// narrowed table as if it were everything, which is the one failure this whole
// consolidation is meant to remove.

export const AUDIT_ACTIONS = ["", "upload", "download", "convert", "compile", "view", "render"];
export const AUDIT_KINDS = ["", "shared", "project", "user"];
export const AUDIT_TARGETS = [
    "",
    "glb",
    "ifc",
    "xml",
    "step",
    "stl",
    "obj",
    "sat",
    "procedural_build",
    "procedural_detail",
];
// States that actually occur on audit_log rows — server-side filter.
//
// The first five are the job lifecycle (queue.py JOB_STATUS_*). ``ok`` and
// ``presigned`` are not jobs at all: they mark instantaneous actions
// (download, delete, view, upload) and URL grants, and on a real deployment
// they outnumber the jobs several times over. They are selectable because they
// are in the log and an operator may want exactly them; the Overview counts
// them separately for the same reason.
export const AUDIT_STATUSES = [
    "",
    "queued",
    "running",
    "done",
    "error",
    "cancelled",
    "ok",
    "presigned",
];

/** Label shown on a chip. The store key is the API's name; these are the
 * operator's. */
const CHIP_LABEL: Record<(typeof AUDIT_FILTER_KEYS)[number], string> = {
    user_sub: "user",
    scope_kind: "kind",
    scope_id: "scope",
    action: "action",
    target: "target",
    status: "state",
    key: "file",
};

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

/** Open by default on a comfortable viewport, closed on a phone. Read once —
 * this is a starting position, not a binding to viewport width, so a resize
 * never yanks the panel open under someone mid-edit. */
function defaultOpen(): boolean {
    if (typeof window === "undefined" || !window.matchMedia) return true;
    return window.matchMedia("(min-width: 640px)").matches;
}

/** ``datetime-local`` gives a local wall-clock string with no zone; the API
 * wants an instant. Going through Date does the conversion the operator means:
 * they picked a time on their own clock. */
function localToIso(v: string): string | undefined {
    if (!v) return undefined;
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

function isoToLocal(v: string | undefined): string {
    if (!v) return "";
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return "";
    // datetime-local wants YYYY-MM-DDTHH:mm in LOCAL time.
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A custom window, written the way an operator reads it. Collapsed, the
 * select can only say "Custom range" — which is the same failure as a hidden
 * chip: the numbers are narrowed and the screen does not say to what. */
function describeCustomRange(since?: string, until?: string): string {
    const fmt = (v?: string) => {
        if (!v) return null;
        const d = new Date(v);
        if (Number.isNaN(d.getTime())) return null;
        return d.toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
        });
    };
    const a = fmt(since);
    const b = fmt(until);
    if (a && b) return `${a} → ${b}`;
    if (a) return `since ${a}`;
    if (b) return `up to ${b}`;
    return "no bounds set";
}

/** An inverted window returns nothing and explains nothing. Worth saying,
 * because the two fields are set independently and the mistake is easy. */
function rangeIsInverted(since?: string, until?: string): boolean {
    if (!since || !until) return false;
    const a = new Date(since).getTime();
    const b = new Date(until).getTime();
    return Number.isFinite(a) && Number.isFinite(b) && a > b;
}

/** True when the window is a bespoke instant pair rather than one of the
 * presets — i.e. the select should read "Custom". */
function isCustomRange(since?: string, until?: string): boolean {
    if (until) return true;
    if (!since) return false;
    return !AUDIT_RANGES.some((r) => r.value === since);
}

const AuditFilterBar: React.FC<{
    /** Shown next to Refresh; the owning sub-tab reports its own busy state. */
    loading?: boolean;
    onRefresh?: () => void;
}> = ({loading = false, onRefresh}) => {
    const filters = useAuditFilterStore((s) => s.filters);
    const patch = useAuditFilterStore((s) => s.patch);
    const reset = useAuditFilterStore((s) => s.reset);
    const [open, setOpen] = useState(defaultOpen);
    const active = countActiveAuditFilters(filters);
    const custom = isCustomRange(filters.since, filters.until);
    const [showCustom, setShowCustom] = useState(custom);

    const chips = AUDIT_FILTER_KEYS.map((k) => [k, filters[k]] as const).filter(
        ([, v]) => v !== undefined && v !== null && v !== "",
    );

    return (
        <div className="border-b border-gray-700">
            <div className="flex items-center gap-2 px-3 sm:px-4 py-2 text-xs">
                <button
                    className="bg-gray-800 hover:bg-gray-700 px-2 py-1 rounded-sm shrink-0"
                    onClick={() => setOpen((v) => !v)}
                    aria-expanded={open}
                    title={open ? "Hide the filter controls" : "Show the filter controls"}
                >
                    Filters{active > 0 ? ` (${active})` : ""} {open ? "▲" : "▼"}
                </button>

                {/* Collapsed: the active filters stay visible, and each chip
                    clears its own field — the fastest way out of a filter you
                    can see is the filter itself. */}
                {!open && chips.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1 min-w-0">
                        {chips.map(([k, v]) => (
                            <button
                                key={k}
                                className="bg-gray-800 border border-gray-700 hover:border-gray-500 rounded-sm px-1.5 py-0.5 font-mono text-[11px] text-gray-200 max-w-[14rem] truncate"
                                onClick={() => patch({[k]: undefined} as Partial<AuditFilters>)}
                                title={`${CHIP_LABEL[k]}=${String(v)} — click to clear`}
                            >
                                {CHIP_LABEL[k]}={String(v)} ×
                            </button>
                        ))}
                    </div>
                )}
                {!open && chips.length === 0 && !custom && (
                    <span className="text-gray-500 truncate">no filter — showing everything</span>
                )}
                {custom && (
                    <span
                        className="font-mono text-[11px] text-gray-300 truncate"
                        title="The custom window currently applied"
                    >
                        {describeCustomRange(filters.since, filters.until)}
                    </span>
                )}

                {/* Always visible, even collapsed: the window changes what every
                    number on this tab means, and a hidden one would leave a
                    six-hour count being read as all of history. */}
                <select
                    className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-white shrink-0"
                    value={custom ? "__custom__" : (filters.since || "")}
                    onChange={(e) => {
                        const v = e.target.value;
                        if (v === "__custom__") {
                            setShowCustom(true);
                            setOpen(true);
                            return;
                        }
                        setShowCustom(false);
                        patch({since: v || undefined, until: undefined});
                    }}
                    title="How far back to count and list"
                >
                    {AUDIT_RANGES.map((r) => (
                        <option key={r.value || "all"} value={r.value}>
                            {r.label}
                        </option>
                    ))}
                    <option value="__custom__">Custom range…</option>
                </select>

                {active > 0 && (
                    <button
                        className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded-sm shrink-0"
                        onClick={reset}
                        title="Clear every filter"
                    >
                        Clear
                    </button>
                )}
                {onRefresh && (
                    <button
                        className="ml-auto bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded-sm shrink-0"
                        onClick={onRefresh}
                        disabled={loading}
                    >
                        {loading ? "Loading…" : "Refresh"}
                    </button>
                )}
            </div>

            {open && (
                <div className="flex flex-wrap gap-2 px-3 sm:px-4 pb-2 text-xs">
                    <FilterInput
                        placeholder="user_sub"
                        value={filters.user_sub || ""}
                        onChange={(v) => patch({user_sub: v || undefined})}
                    />
                    <FilterSelect
                        options={AUDIT_KINDS}
                        value={filters.scope_kind || ""}
                        onChange={(v) => patch({scope_kind: v || undefined})}
                        placeholder="any kind"
                    />
                    <FilterInput
                        placeholder="scope_id"
                        value={filters.scope_id || ""}
                        onChange={(v) => patch({scope_id: v || undefined})}
                    />
                    <FilterSelect
                        options={AUDIT_ACTIONS}
                        value={filters.action || ""}
                        onChange={(v) => patch({action: v || undefined})}
                        placeholder="any action"
                    />
                    <FilterSelect
                        options={AUDIT_TARGETS}
                        value={filters.target || ""}
                        onChange={(v) => patch({target: v || undefined})}
                        placeholder="any target"
                    />
                    <FilterSelect
                        options={AUDIT_STATUSES}
                        value={filters.status || ""}
                        onChange={(v) => patch({status: v || undefined})}
                        placeholder="any state"
                    />
                    <FilterInput
                        placeholder="filename / path…"
                        value={filters.key || ""}
                        onChange={(v) => patch({key: v || undefined})}
                    />
                    {(showCustom || custom) && (
                        <span className="flex items-center gap-1 text-gray-400">
                            <span className="text-[11px] uppercase tracking-wide">from</span>
                            <input
                                type="datetime-local"
                                className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-white"
                                value={isoToLocal(custom ? filters.since : undefined)}
                                onChange={(e) => patch({since: localToIso(e.target.value)})}
                            />
                            <span className="text-[11px] uppercase tracking-wide">to</span>
                            <input
                                type="datetime-local"
                                className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-white"
                                value={isoToLocal(filters.until)}
                                onChange={(e) => patch({until: localToIso(e.target.value)})}
                            />
                            {rangeIsInverted(filters.since, filters.until) && (
                                <span className="text-amber-300" role="status">
                                    ends before it starts — nothing can match
                                </span>
                            )}
                        </span>
                    )}
                </div>
            )}
        </div>
    );
};

export default AuditFilterBar;
