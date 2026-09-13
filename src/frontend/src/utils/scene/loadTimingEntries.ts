// Performance-entry bookkeeping for LoadMetricsRecorder, kept free of the
// renderer and stores so it can be unit-tested.

export interface LongTaskTotals {
    count: number;
    ms: number;
    blockingMs: number;
}

export function emptyLongTaskTotals(): LongTaskTotals {
    return {count: 0, ms: 0, blockingMs: 0};
}

/** Add the long tasks that started at or after `since` to `totals`.
 *
 * The recorder's longtask observer is registered with `buffered: true` so a
 * task already queued when the load begins isn't missed, but that also replays
 * every long task since page load. Without the filter each load reports the
 * whole session's main-thread jank, growing load after load. */
export function addLongTasks(
    totals: LongTaskTotals,
    entries: Iterable<{startTime: number; duration: number}>,
    since: number,
): void {
    for (const e of entries) {
        if (e.startTime < since) continue;
        totals.count += 1;
        totals.ms += e.duration;
        totals.blockingMs += Math.max(0, e.duration - 50);
    }
}

/** The Resource Timing entry for `url`: the latest one a PerformanceObserver
 * delivered, else the latest in the global buffer.
 *
 * `performance.getEntriesByName()` only sees the global Resource Timing buffer,
 * which holds 250 entries by default; once a long session (a gallery walk)
 * fills it, new fetches are no longer added and every later load loses its
 * network split. Observers still receive those entries. */
export function findResourceEntry<T extends {name: string}>(
    observed: readonly T[],
    url: string,
    buffered: () => readonly T[],
): T | undefined {
    for (let i = observed.length - 1; i >= 0; i--) {
        if (observed[i].name === url) return observed[i];
    }
    const fromBuffer = buffered();
    return fromBuffer[fromBuffer.length - 1];
}
