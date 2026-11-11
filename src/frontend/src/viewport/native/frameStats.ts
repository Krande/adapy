// Frame timing for the three.js-free viewport's stats panel: a ring of recent frames and what they add
// up to. It renders on demand, so a gap between frames is not a slow frame: only frames that follow
// another within RUN_GAP_MS count toward the rate and the frame times. Plain numbers, no three.js.

/** A longer gap than this ends a run of frames: the viewport was idle, not slow. */
export const RUN_GAP_MS = 250;
/** No frame for this long: the viewport is idle. */
export const IDLE_MS = 500;
export const FRAME_CAPACITY = 240;

export interface FrameRing {
    /** When each frame started; NaN where none was recorded. */
    at: Float64Array;
    /** Its interval from the previous frame of the same run; NaN for the first frame of a run. */
    interval: Float64Array;
    cpuMs: Float64Array;
    gpuMs: Float64Array;
    /** The slot the next frame is written to. */
    next: number;
    count: number;
}

export function createFrameRing(capacity = FRAME_CAPACITY): FrameRing {
    return {
        at: new Float64Array(capacity).fill(NaN),
        interval: new Float64Array(capacity).fill(NaN),
        cpuMs: new Float64Array(capacity).fill(NaN),
        gpuMs: new Float64Array(capacity).fill(NaN),
        next: 0,
        count: 0,
    };
}

export function recordFrame(ring: FrameRing, at: number, cpuMs: number, gpuMs: number): void {
    const n = ring.at.length;
    const prev = ring.count ? ring.at[(ring.next + n - 1) % n] : NaN;
    if (at === prev) return; // the same frame, seen twice
    const gap = at - prev;
    ring.at[ring.next] = at;
    ring.interval[ring.next] = gap > 0 && gap <= RUN_GAP_MS ? gap : NaN;
    ring.cpuMs[ring.next] = cpuMs;
    ring.gpuMs[ring.next] = gpuMs;
    ring.next = (ring.next + 1) % n;
    if (ring.count < n) ring.count++;
}

export interface FrameSummary {
    idle: boolean;
    /** Frames per second over the last second of drawing; null when idle or too few frames. */
    fps: number | null;
    /** Frame interval percentiles of the frames kept (ms); null without intervals. */
    p50: number | null;
    p95: number | null;
    max: number | null;
    cpuMs: number | null;
    gpuMs: number | null;
}

function percentile(sorted: number[], q: number): number {
    return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
}

function mean(values: number[]): number | null {
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

export function summarize(ring: FrameRing, now: number): FrameSummary {
    const n = ring.at.length;
    const last = ring.count ? ring.at[(ring.next + n - 1) % n] : NaN;
    const idle = !(now - last <= IDLE_MS);
    const intervals: number[] = [], cpu: number[] = [], gpu: number[] = [];
    let recent = 0, recentSpan = 0;
    for (let k = 0; k < ring.count; k++) {
        const i = (ring.next + n - 1 - k) % n;
        const dt = ring.interval[i];
        if (!Number.isNaN(dt)) {
            intervals.push(dt);
            if (now - ring.at[i] <= 1000) {
                recent++;
                recentSpan += dt;
            }
        }
        if (!Number.isNaN(ring.cpuMs[i])) cpu.push(ring.cpuMs[i]);
        if (ring.gpuMs[i] > 0) gpu.push(ring.gpuMs[i]);
    }
    intervals.sort((a, b) => a - b);
    return {
        idle,
        fps: !idle && recent >= 2 ? (1000 * recent) / recentSpan : null,
        p50: intervals.length ? percentile(intervals, 0.5) : null,
        p95: intervals.length ? percentile(intervals, 0.95) : null,
        max: intervals.length ? intervals[intervals.length - 1] : null,
        cpuMs: mean(cpu),
        gpuMs: mean(gpu),
    };
}

/** The intervals oldest first, for the sparkline; NaN where a run started. */
export function intervalsInOrder(ring: FrameRing): number[] {
    const n = ring.at.length;
    const out: number[] = [];
    for (let k = ring.count - 1; k >= 0; k--) out.push(ring.interval[(ring.next + n - 1 - k) % n]);
    return out;
}
