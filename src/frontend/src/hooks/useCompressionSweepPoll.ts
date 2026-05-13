// Background poll of /api/admin/storage/compression-status. Only
// active when the current user is an admin; server-side state lives
// in NATS KV so a page reload (or a different browser session) picks
// up an in-flight sweep that someone else's session started.
//
// Polling cadence:
//   * 5 s when any sweep is in-progress on the server (or just
//     finished — keeps the toast visible for a few cycles so the
//     user sees the result).
//   * 30 s otherwise — cheap "did someone else start one?" check.
//
// Cleared automatically when the user navigates away; no need to
// thread cancellation into the store.

import {useEffect, useRef} from "react";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {useCompressionStore} from "@/state/compressionStore";
import {useMeStore} from "@/state/meStore";

export function useCompressionSweepPoll() {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const setSweeps = useCompressionStore((s) => s.setSweeps);
    const lastIntervalRef = useRef<number | null>(null);

    useEffect(() => {
        if (!isAdmin) {
            // Non-admin sessions: nothing to render, nothing to poll.
            return;
        }

        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | null = null;

        const tick = async () => {
            if (cancelled) return;
            try {
                const r = await viewerApi.adminCompressionStatus();
                if (cancelled) return;
                setSweeps(r.scopes);
                const anyActive = Object.values(r.scopes).some(
                    (s) => s.completed_at === null && !s.orphaned,
                );
                lastIntervalRef.current = anyActive ? 5000 : 30000;
            } catch (err) {
                // 401 means the bearer expired (or auth state is being
                // refreshed). Stop polling entirely — repeating the
                // request just floods the console with the same 401.
                // Auth refresh / re-login remounts the hook with a
                // fresh token, which re-arms the loop.
                if (err instanceof ApiError && err.status === 401) {
                    return;
                }
                // 503 (NATS unavailable) / network blip — back off briefly
                // and try again; don't surface this as a user error.
                lastIntervalRef.current = 30000;
            }
            if (!cancelled) {
                timer = setTimeout(tick, lastIntervalRef.current ?? 30000);
            }
        };

        void tick();
        return () => {
            cancelled = true;
            if (timer !== null) clearTimeout(timer);
        };
    }, [isAdmin, setSweeps]);
}
