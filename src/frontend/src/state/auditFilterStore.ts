import {create} from "zustand";

import type {AuditFilters} from "@/services/viewerApi";

// The filter shared by the Audit tab's job-shaped sub-tabs (Overview, Log,
// Runs).
//
// It lives in a store rather than in AuditLogTab's own state because the
// filter is now the tab's spine: Overview counts a population, the operator
// clicks a count, and Log shows exactly that population. Owning it in one
// sub-tab would mean the drill-down had to hand state sideways to a sibling,
// and the two surfaces could disagree about what "filtered" means.
//
// Not persisted, deliberately. A status filter is a step in an investigation,
// not a preference — coming back tomorrow to an admin panel silently showing
// only failures is a support ticket, not a convenience.
/** Auto-refresh choices. Off is the default and deliberately so: a panel that
 * silently re-polls forever is a background load nobody asked for, and on a
 * shared deployment it is multiplied by whoever left a tab open.
 *
 * Nothing faster than 5s — the summary is an aggregate over the whole table,
 * and a 1s poll would spend more of the API's time counting than converting. */
export const AUDIT_REFRESH_INTERVALS: {value: number; label: string}[] = [
    {value: 0, label: "Off"},
    {value: 5_000, label: "5s"},
    {value: 15_000, label: "15s"},
    {value: 30_000, label: "30s"},
    {value: 60_000, label: "1m"},
    {value: 300_000, label: "5m"},
];

export interface AuditFilterState {
    filters: AuditFilters;
    /** Bumped to ask every sub-tab to reload without changing the filter.
     *
     * A counter rather than a callback registry: the sub-tabs already reload on
     * a filter change, so refresh is the same effect with one more dependency,
     * and nothing has to know which panels are mounted. */
    refreshNonce: number;
    /** Milliseconds between automatic refreshes; 0 is off. */
    autoRefreshMs: number;
    /** When the data was last asked for, so the bar can say how stale it is. */
    lastRefreshedAt: number;
    refresh: () => void;
    setAutoRefreshMs: (ms: number) => void;
    /** Merge a partial change (the usual case — one control moved). */
    patch: (next: Partial<AuditFilters>) => void;
    /** Replace wholesale. */
    set: (next: AuditFilters) => void;
    reset: () => void;
    /** Set, or unset when already set — what the Overview status tiles do. */
    toggleStatus: (status: string) => void;
}

/** Page size for the log. Kept here so the filter object handed to the API is
 * complete and callers never have to remember to add it. */
export const AUDIT_PAGE_LIMIT = 100;

const EMPTY: AuditFilters = {limit: AUDIT_PAGE_LIMIT};

/** The time window presets, coarse to fine.
 *
 * The rungs are the ones an operator actually reaches for: "is it running
 * right now" (5m/15m), "what happened this shift" (1h/6h/24h), and "is this
 * new" (7d/30d). 15m earns its place because 5m to 1h is the biggest jump in
 * the ladder and it is the standard next rung in every observability tool.
 *
 * The value is sent verbatim and resolved by the server — see AuditFilters.since. */
export const AUDIT_RANGES: {value: string; label: string}[] = [
    {value: "", label: "All time"},
    {value: "30d", label: "Last 30 days"},
    {value: "7d", label: "Last 7 days"},
    {value: "24h", label: "Last 24 hours"},
    {value: "6h", label: "Last 6 hours"},
    {value: "1h", label: "Last hour"},
    {value: "15m", label: "Last 15 minutes"},
    {value: "5m", label: "Last 5 minutes"},
];

/** Filter keys the operator can actually set — i.e. everything except the
 * paging machinery. Used for "is anything filtered?" and for the chip row.
 *
 * ``since``/``until`` are deliberately absent: the range has its own control
 * that stays visible even when the bar is collapsed, so it can never hide the
 * way a chip could. Counting it in the badge as well would double-report it. */
export const AUDIT_FILTER_KEYS = [
    "user_sub",
    "scope_kind",
    "scope_id",
    "action",
    "target",
    "status",
    "key",
] as const;

export function countActiveAuditFilters(f: AuditFilters): number {
    return AUDIT_FILTER_KEYS.filter((k) => {
        const v = f[k];
        return v !== undefined && v !== null && v !== "";
    }).length;
}

export const useAuditFilterStore = create<AuditFilterState>()((set) => ({
    filters: EMPTY,
    refreshNonce: 0,
    autoRefreshMs: 0,
    lastRefreshedAt: Date.now(),
    refresh: () => set((s) => ({refreshNonce: s.refreshNonce + 1, lastRefreshedAt: Date.now()})),
    setAutoRefreshMs: (autoRefreshMs) => set({autoRefreshMs}),
    // ``before_id`` is a page cursor, never part of a filter change: keeping
    // it would ask the server to continue paging a result set that no longer
    // exists. Every mutation below drops it.
    patch: (next) =>
        set((s) => ({
            filters: {...s.filters, ...next, before_id: undefined},
            lastRefreshedAt: Date.now(),
        })),
    set: (next) => set({filters: {...next, before_id: undefined}, lastRefreshedAt: Date.now()}),
    reset: () => set({filters: EMPTY, lastRefreshedAt: Date.now()}),
    toggleStatus: (status) =>
        set((s) => ({
            lastRefreshedAt: Date.now(),
            filters: {
                ...s.filters,
                status: s.filters.status === status ? undefined : status,
                before_id: undefined,
            },
        })),
}));
