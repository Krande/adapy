// Thin wrapper over the JS Self-Profiling API (Chromium), shared by the
// load recorder (loadMetrics) and the render profiler (renderProfiler).
//
// It samples the main-thread call stack at a fixed interval and, on stop,
// reduces the trace to a top-N self-time table — capturing BOTH TypeScript
// frames and WASM frames (pyodide / adacpp surface as ``wasm-function[...]``
// or their name-section names). This is how a CPU/main-thread bottleneck is
// pinned to a function. It does NOT see GPU execution (rasterization /
// fragment shading) — that's what the render profiler's GPU timer query is
// for.
//
// Needs the document served with ``Document-Policy: js-profiling`` (the
// viewer sets it). Construction throws otherwise; ``isCallProfilingAvailable``
// and the guarded start() degrade to a no-op so callers never break.

export interface ProfileFrame {
    fn: string;
    self_ms: number;
    total_ms: number;
}

// Minimal shape of the JS Self-Profiling API (not in the TS DOM lib yet).
interface ProfilerTrace {
    frames: {name?: string; resourceId?: number; line?: number; column?: number}[];
    resources: string[];
    stacks: {frameId: number; parentId?: number}[];
    samples: {stackId?: number; timestamp: number}[];
}
interface ProfilerInstance {
    stop(): Promise<ProfilerTrace>;
}
interface ProfilerCtor {
    new (opts: {sampleInterval: number; maxBufferSize: number}): ProfilerInstance;
}

export function isCallProfilingAvailable(): boolean {
    return typeof (window as unknown as {Profiler?: ProfilerCtor}).Profiler === "function";
}

/** Top-N self-time frames from a trace. Self time = a frame is the leaf of
 * the sampled stack; total = it appears anywhere in the stack. */
function summarizeProfile(trace: ProfilerTrace, sampleIntervalMs: number, topN: number): ProfileFrame[] {
    try {
        const self = new Map<number, number>();
        const total = new Map<number, number>();
        for (const s of trace.samples) {
            if (s.stackId == null) continue;
            let node: {frameId: number; parentId?: number} | undefined = trace.stacks[s.stackId];
            let isLeaf = true;
            const seen = new Set<number>();
            while (node) {
                const fid = node.frameId;
                if (isLeaf) self.set(fid, (self.get(fid) || 0) + 1);
                if (!seen.has(fid)) {
                    total.set(fid, (total.get(fid) || 0) + 1);
                    seen.add(fid);
                }
                isLeaf = false;
                node = node.parentId != null ? trace.stacks[node.parentId] : undefined;
            }
        }
        const nameOf = (fid: number): string => {
            const f = trace.frames[fid];
            if (!f) return "(anonymous)";
            const res = f.resourceId != null ? trace.resources[f.resourceId] : undefined;
            const base = f.name && f.name.length > 0 ? f.name : "(anonymous)";
            if (res) {
                const file = res.split("/").pop()?.split("?")[0] || res;
                return f.line != null ? `${base} (${file}:${f.line})` : `${base} (${file})`;
            }
            return base;
        };
        return [...self.entries()]
            .map(([fid, count]) => ({
                fn: nameOf(fid),
                self_ms: Math.round(count * sampleIntervalMs * 10) / 10,
                total_ms: Math.round((total.get(fid) || 0) * sampleIntervalMs * 10) / 10,
            }))
            .sort((a, b) => b.self_ms - a.self_ms)
            .slice(0, topN);
    } catch {
        return [];
    }
}

/** A start/stop sampling profiler. ``start()`` is a no-op if the API or
 * policy is missing; ``stop()`` then returns ``[]``. Safe to construct and
 * call unconditionally. */
export class CallProfiler {
    private profiler: ProfilerInstance | null = null;
    constructor(private readonly sampleIntervalMs = 10, private readonly maxBufferSize = 100000) {}

    start(): void {
        if (this.profiler) return;
        try {
            const Ctor = (window as unknown as {Profiler?: ProfilerCtor}).Profiler;
            if (Ctor) {
                this.profiler = new Ctor({sampleInterval: this.sampleIntervalMs, maxBufferSize: this.maxBufferSize});
            }
        } catch {
            this.profiler = null;
        }
    }

    async stop(topN = 40): Promise<ProfileFrame[]> {
        const p = this.profiler;
        this.profiler = null;
        if (!p) return [];
        try {
            const trace = await p.stop();
            return summarizeProfile(trace, this.sampleIntervalMs, topN);
        } catch {
            return [];
        }
    }

    get active(): boolean {
        return this.profiler != null;
    }
}
