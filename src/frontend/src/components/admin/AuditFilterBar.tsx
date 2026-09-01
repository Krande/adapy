import React, {useEffect, useState} from "react";

import type {AuditFilters} from "@/services/viewerApi";
import {
    AUDIT_FILTER_KEYS,
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
// Job states the queue writes (queue.py JOB_STATUS_*) — server-side filter.
export const AUDIT_STATUSES = ["", "queued", "running", "done", "error"];

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
                {!open && chips.length === 0 && (
                    <span className="text-gray-500 truncate">no filter — showing everything</span>
                )}

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
                </div>
            )}
        </div>
    );
};

export default AuditFilterBar;
