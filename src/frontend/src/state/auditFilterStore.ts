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
export interface AuditFilterState {
    filters: AuditFilters;
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
    // ``before_id`` is a page cursor, never part of a filter change: keeping
    // it would ask the server to continue paging a result set that no longer
    // exists. Every mutation below drops it.
    patch: (next) =>
        set((s) => ({filters: {...s.filters, ...next, before_id: undefined}})),
    set: (next) => set({filters: {...next, before_id: undefined}}),
    reset: () => set({filters: EMPTY}),
    toggleStatus: (status) =>
        set((s) => ({
            filters: {
                ...s.filters,
                status: s.filters.status === status ? undefined : status,
                before_id: undefined,
            },
        })),
}));
