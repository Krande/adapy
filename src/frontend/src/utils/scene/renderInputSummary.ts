// The render profiler's per-window summaries of main-thread long tasks and of
// 3D-mouse input. Pure, so the overlap arithmetic is testable on its own.

/** A main-thread task over 50 ms, as [start, end] in performance.now() ms. */
export type Span = readonly [number, number];

export interface InputWindow {
    connection: string;
    device: string | null;
    product_id: string | null;
    hidReports: number;
    wheels: number;
    gaps: readonly number[];
    slowGaps: readonly Span[];
    gestures: number;
    staleCuts: number;
}

function pct(sorted: readonly number[], p: number): number | null {
    if (sorted.length === 0) return null;
    const i = Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))));
    return Math.round(sorted[i] * 10) / 10;
}

function overlap(a: Span, b: Span): number {
    return Math.max(0, Math.min(a[1], b[1]) - Math.max(a[0], b[0]));
}

/** Count and time of the window's long tasks. */
export function summariseLongTasks(tasks: readonly Span[]): Record<string, number> {
    let total = 0;
    let max = 0;
    for (const t of tasks) {
        const d = t[1] - t[0];
        total += d;
        if (d > max) max = d;
    }
    return {long_tasks: tasks.length, long_task_ms: Math.round(total), long_task_max_ms: Math.round(max)};
}

/**
 * The device's report cadence while the cap was held, and how much of its slow
 * gaps a long task covered: a gap mostly covered was the main thread holding
 * the reports back; an uncovered one was the device or its link sending none.
 */
export function summariseInput(win: InputWindow, tasks: readonly Span[]): Record<string, unknown> {
    const sorted = [...win.gaps].sort((a, b) => a - b);
    let blocked = 0;
    let slowMs = 0;
    let coveredMs = 0;
    for (const g of win.slowGaps) {
        const len = g[1] - g[0];
        let covered = 0;
        for (const t of tasks) covered += overlap(g, t);
        covered = Math.min(covered, len);
        slowMs += len;
        coveredMs += covered;
        if (covered >= len / 2) blocked++;
    }
    return {
        connection: win.connection,
        device: win.device,
        product_id: win.product_id,
        hid_reports: win.hidReports,
        wheels: win.wheels,
        reports: win.gaps.length,
        gap_ms_p50: pct(sorted, 0.5),
        gap_ms_p95: pct(sorted, 0.95),
        gap_ms_max: pct(sorted, 1),
        slow_gaps: win.slowGaps.length,
        slow_gaps_blocked: blocked,
        slow_gap_ms: Math.round(slowMs),
        slow_gap_ms_in_long_tasks: Math.round(coveredMs),
        gestures: win.gestures,
        stale_cuts: win.staleCuts,
    };
}
