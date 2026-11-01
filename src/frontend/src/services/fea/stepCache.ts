// One byte budget for every per-step FEA value the viewer keeps in memory.
//
// The range-fetched steps of nodal fields (feaFieldBlob) and element fields
// (feaElemFieldBlob) used to live in two unbounded Maps. Stepping through a model
// one frame at a time is exactly what fills them: playing the 70 frames of a
// 255k-node shell model in stress keeps ~4 MB of stress and ~3 MB of displacement
// per frame, half a gigabyte by the end of one loop, and nothing ever let go.
//
// `StepCache` is a Map of promises with a least-recently-used byte budget shared by
// both caches. A promise counts once it resolves (its Float32Array's byte length);
// one that is still in flight is never evicted, so a read-ahead cannot be thrown
// away before it lands. Hitting an entry moves it to the back of the queue.

/** Bytes the step caches may hold together. Scaled from the device's memory where
 *  the browser reports it (Chrome's `navigator.deviceMemory`, in GB, capped at 8),
 *  else a middle value -- Safari does not say. */
export function defaultStepCacheBudget(): number {
    const MB = 1024 * 1024;
    const gb = typeof navigator !== "undefined"
        ? (navigator as unknown as {deviceMemory?: number}).deviceMemory
        : undefined;
    if (!gb || !Number.isFinite(gb)) return 384 * MB;
    return Math.min(1024, Math.max(128, gb * 128)) * MB;
}

interface Entry {
    promise: Promise<Float32Array>;
    bytes: number;
}

export class StepCache {
    private readonly entries = new Map<string, Entry>();
    private total = 0;

    constructor(private budget: number = defaultStepCacheBudget()) {}

    get(key: string): Promise<Float32Array> | undefined {
        const entry = this.entries.get(key);
        if (!entry) return undefined;
        // Most recently used goes last; eviction starts at the front.
        this.entries.delete(key);
        this.entries.set(key, entry);
        return entry.promise;
    }

    has(key: string): boolean {
        return this.entries.has(key);
    }

    set(key: string, promise: Promise<Float32Array>): void {
        this.delete(key);
        const entry: Entry = {promise, bytes: 0};
        this.entries.set(key, entry);
        promise.then(
            (values) => {
                // Replaced or dropped while in flight: not ours to count.
                if (this.entries.get(key) !== entry) return;
                entry.bytes = values.byteLength;
                this.total += entry.bytes;
                this.evict(key);
            },
            () => undefined,
        );
    }

    delete(key: string): void {
        const entry = this.entries.get(key);
        if (!entry) return;
        this.total -= entry.bytes;
        this.entries.delete(key);
    }

    clear(): void {
        this.entries.clear();
        this.total = 0;
    }

    /** Bytes held by resolved entries. */
    get bytes(): number {
        return this.total;
    }

    get budgetBytes(): number {
        return this.budget;
    }

    setBudget(bytes: number): void {
        this.budget = bytes;
        this.evict(null);
    }

    /** Drop least-recently-used resolved entries until the total fits, never the
     *  one just added (`keep`) and never one still in flight. */
    private evict(keep: string | null): void {
        if (this.total <= this.budget) return;
        for (const [key, entry] of this.entries) {
            if (this.total <= this.budget) break;
            if (key === keep || entry.bytes === 0) continue;
            this.total -= entry.bytes;
            this.entries.delete(key);
        }
    }
}

/** The one cache both blob modules use: the budget is for the viewer, not per kind. */
export const FEA_STEP_CACHE = new StepCache();
