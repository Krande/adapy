// Pure scheduling for a STEP / IFC -> GLB conversion split across several workers (cadGlbShardPool.ts
// runs it). No DOM, no workers: what to run next, given what has finished.
//
// Order, pulled by whichever worker is idle:
//   1. the face ranges of the HUGE roots -- a root bigger than one worker's share, split by face range
//      so no worker carries it alone (the slowest single root is often most of a file's work);
//   2. a huge root's assembly (join + weld its parts) as soon as every one of its ranges is done;
//   3. the ordinary roots in batches of about equal estimated cost, heaviest first.
// A worker with nothing to take while an assembly is still pending waits; when everything is done
// every worker persists, and one merges.

export type ShardTask =
    | {kind: "huge"; h: number; f0: number; f1: number}
    | {kind: "assemble"; h: number; chunk: number}
    | {kind: "batch"; begin: number; end: number};

export class ShardScheduler {
    private ranges: {h: number; f0: number; f1: number}[] = [];
    private chunks: number[] = [];
    private left: number[] = []; // ranges not yet done, per huge root
    private assemblies: number[] = [];
    private pendingAssemblies: number;
    private nextBatch = 0;
    private running = 0;
    /** Every task this schedule will hand out (for progress). */
    readonly total: number;

    /** `huge`: face count per huge root; `ends`: exclusive end of each ordinary-root batch. */
    constructor(
        readonly workers: number,
        huge: number[],
        private readonly ends: number[],
    ) {
        huge.forEach((nf, h) => {
            // about four ranges per worker, never fewer than 64 faces each
            const chunk = Math.max(64, Math.ceil(nf / (workers * 4)));
            let n = 0;
            for (let f0 = 0; f0 < nf; f0 += chunk, n++) this.ranges.push({h, f0, f1: Math.min(nf, f0 + chunk)});
            this.chunks.push(chunk);
            this.left.push(n);
            if (n === 0) this.assemblies.push(h); // an empty huge root still bakes (no geometry)
        });
        this.pendingAssemblies = huge.length;
        this.total = this.ranges.length + huge.length + ends.length;
    }

    /** The next task, `"wait"` while an assembly another worker unblocks is pending, or `"done"`. */
    next(): ShardTask | "wait" | "done" {
        const h = this.assemblies.shift();
        if (h !== undefined) {
            this.running++;
            return {kind: "assemble", h, chunk: this.chunks[h]};
        }
        const r = this.ranges.shift();
        if (r) {
            this.running++;
            return {kind: "huge", ...r};
        }
        if (this.nextBatch < this.ends.length) {
            const begin = this.nextBatch ? this.ends[this.nextBatch - 1] : 0;
            const end = this.ends[this.nextBatch++];
            this.running++;
            return {kind: "batch", begin, end};
        }
        return this.pendingAssemblies > 0 || this.running > 0 ? "wait" : "done";
    }

    /** Report a task finished; may make an assembly available. */
    complete(task: ShardTask): void {
        this.running--;
        if (task.kind === "huge" && --this.left[task.h] === 0) this.assemblies.push(task.h);
        if (task.kind === "assemble") this.pendingAssemblies--;
    }
}

/** How many workers to split a conversion of `sourceBytes` across: one per core but one (the page
 *  keeps a core), bounded by a memory budget -- each worker is its own wasm instance, so the tab's
 *  memory grows with the count -- and by `max`. 1 means "don't shard". */
export function shardWorkerCount(
    sourceBytes: number,
    env: {cores?: number; deviceMemoryGb?: number; max?: number} = {},
): number {
    const cores = env.cores ?? 4;
    // navigator.deviceMemory is coarse and capped (Chromium reports at most 8); half of it for the job.
    const budgetMb = (env.deviceMemoryGb ?? 4) * 1024 * 0.5;
    // Measured per-worker wasm memory, worst case: ~0.5x the source size above a ~256 MB floor.
    const perWorkerMb = 256 + 0.5 * (sourceBytes / (1024 * 1024));
    const byMemory = Math.floor(budgetMb / perWorkerMb);
    return Math.max(1, Math.min(cores - 1, byMemory, env.max ?? 8));
}
