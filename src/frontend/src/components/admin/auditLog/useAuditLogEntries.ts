import {useEffect, useState} from "react";
import {ApiError, AuditEntry, AuditFilters, viewerApi} from "@/services/viewerApi";
import {useAuditFilterStore} from "@/state/auditFilterStore";

// Fetches the audit log for the shared filter. Pagination is keyset on the
// BIGSERIAL id (the server returns next_before_id) — that way the table
// doesn't shift while new audit rows are inserted between pages.
//
// The filter is owned by the Audit tab, not by this sub-tab: Overview counts
// the same population and the operator drills from one into the other. See
// state/auditFilterStore.
export function useAuditLogEntries() {
    const filters = useAuditFilterStore((st) => st.filters);
    const refreshNonce = useAuditFilterStore((st) => st.refreshNonce);
    const [entries, setEntries] = useState<AuditEntry[]>([]);
    const [nextBeforeId, setNextBeforeId] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

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

    // Reacts to the shared filter rather than owning it: the bar lives in
    // AuditTab, and Overview's tiles write to the same store, so a drill-down
    // arrives here as a filter change like any other.
    useEffect(() => {
        void reload(filters);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [filters, refreshNonce]);

    return {
        filters,
        entries,
        nextBeforeId,
        loading,
        error,
        setError,
        reload: () => reload(filters),
        loadMore,
    };
}
