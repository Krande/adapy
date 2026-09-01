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

/** Filter keys the operator can actually set — i.e. everything except the
 * paging machinery. Used for "is anything filtered?" and for the chip row. */
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
