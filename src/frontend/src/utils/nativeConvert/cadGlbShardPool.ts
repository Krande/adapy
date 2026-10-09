// Main-thread coordinator for a STEP / IFC -> GLB conversion split across several Web Workers, each its
// own instance of the native adacpp module (cadGlbShard.worker.ts). The workers share nothing but an
// OPFS job directory -- no SharedArrayBuffer, no cross-origin isolation -- and pull work from one
// schedule (shardSchedule.ts): the huge roots by face range first, then cost-balanced batches.
//
// Only sharding needs this; one worker is the existing cadGlbConverter path. Anything this browser or
// module cannot do throws ShardingUnavailable before any work starts, so the caller falls back to it.

import * as Comlink from "comlink";

import CadGlbShardWorker from "./cadGlbShard.worker.ts?worker&inline";
import type {CadGlbShardAPI, ShardOpenInfo} from "./cadGlbShard.worker";
import type {CadKind, NativeCadGlbResult} from "./cadGlbConverter.worker";
import {ShardScheduler, type ShardTask} from "./shardSchedule";

/** This browser / adacpp release can't shard (no `glbShards`, no OPFS sync access handles, or no
 *  shared read-only handles): use the single-worker converter instead. */
export class ShardingUnavailable extends Error {}

const JOBS_DIR = "adacpp-shards"; // under the OPFS root; one subdirectory per conversion
const LOCK = "adacpp-glb-shards"; // one sharded conversion per origin at a time (every tab)
const SPAWN_TIMEOUT_MS = 5000;
const SPAWN_ATTEMPTS = 3;
const BATCHES_PER_WORKER = 32;

interface ShardWorker {
    worker: Worker;
    api: Comlink.Remote<CadGlbShardAPI>;
    crashed: Promise<never>;
}

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
    return new Promise((resolve, reject) => {
        const t = setTimeout(() => reject(new Error(`${what} timed out after ${ms} ms`)), ms);
        p.then(
            (v) => {
                clearTimeout(t);
                resolve(v);
            },
            (e) => {
                clearTimeout(t);
                reject(e);
            },
        );
    });
}

// A worker that started but never answers is replaced: several workers starting at once occasionally
// leave one that never runs its script.
async function spawn(): Promise<ShardWorker> {
    let last: unknown;
    for (let attempt = 0; attempt < SPAWN_ATTEMPTS; attempt++) {
        const worker = new CadGlbShardWorker();
        const api = Comlink.wrap<CadGlbShardAPI>(worker);
        // A crashed worker never answers a Comlink call: race every call against its error event.
        const crashed = new Promise<never>((_, reject) => {
            worker.addEventListener("error", (e) => reject(new Error(`shard worker crashed: ${e.message || e}`)));
            worker.addEventListener("messageerror", () => reject(new Error("shard worker message error")));
        });
        crashed.catch(() => undefined); // observed through call(); never an unhandled rejection
        try {
            await withTimeout(Promise.race([api.ping(), crashed]), SPAWN_TIMEOUT_MS, "shard worker start");
            return {worker, api, crashed};
        } catch (e) {
            last = e;
            worker.terminate();
        }
    }
    throw new ShardingUnavailable(`could not start a shard worker: ${last}`);
}

const call = <T>(w: ShardWorker, p: Promise<T>): Promise<T> => Promise.race([p, w.crashed]);

async function jobsRoot(): Promise<FileSystemDirectoryHandle> {
    const root = await navigator.storage.getDirectory();
    return root.getDirectoryHandle(JOBS_DIR, {create: true});
}

async function readGlb(job: string): Promise<ArrayBuffer> {
    const dir = await (await jobsRoot()).getDirectoryHandle(job);
    const file = await (await dir.getFileHandle("out.glb")).getFile();
    return file.arrayBuffer();
}

/** Clear every job directory -- orphans of a closed tab or a crashed run. Only under the lock: without
 *  Web Locks another tab's live job could be in there. */
async function clearOrphanJobs(): Promise<void> {
    if (!navigator.locks) return;
    const root = await navigator.storage.getDirectory();
    await root.removeEntry(JOBS_DIR, {recursive: true}).catch(() => undefined);
}

async function removeJob(job: string): Promise<void> {
    const dir = await jobsRoot().catch(() => null);
    await dir?.removeEntry(job.slice(JOBS_DIR.length + 1), {recursive: true}).catch(() => undefined);
}

/** Where a sharded conversion spent its time (ms), for the audit row. */
export interface ShardedPhases {
    start: number; // spawn + load the modules + mount
    stage: number; // the source into OPFS
    prepare: number; // scan + metadata + LPT order, once
    open: number; // every worker loads the index
    convert: number; // the scheduled work
    persist: number; // lanes to the job directory
    merge: number; // lanes -> GLB
    read: number; // GLB off OPFS
}

export type ShardedResult = NativeCadGlbResult & {workers: number; phases: ShardedPhases};

export interface ShardedProgress {
    done: number;
    total: number;
}

/** Convert a STEP / IFC across `nworkers` workers. `source` is a (presigned) URL the first worker
 *  streams into OPFS, or the bytes (transferred). Throws ShardingUnavailable when this browser or the
 *  deployed module cannot shard -- before any conversion work -- so the caller can fall back. */
export async function nativeCadToGlbSharded(
    kind: CadKind,
    source: {url: string} | {bytes: ArrayBuffer},
    nworkers: number,
    opts: {deflection: number; angularDeg: number; meshopt: boolean},
    onProgress?: (p: ShardedProgress) => void,
): Promise<ShardedResult> {
    if (typeof navigator === "undefined" || !navigator.storage?.getDirectory) {
        throw new ShardingUnavailable("no OPFS in this browser");
    }
    const run = () => convert(kind, source, nworkers, opts, onProgress);
    return navigator.locks ? navigator.locks.request(LOCK, run) : run();
}

async function convert(
    kind: CadKind,
    source: {url: string} | {bytes: ArrayBuffer},
    n: number,
    opts: {deflection: number; angularDeg: number; meshopt: boolean},
    onProgress?: (p: ShardedProgress) => void,
): Promise<ShardedResult> {
    const t0 = performance.now();
    const phases = {} as ShardedPhases;
    let tp = t0;
    const lap = (k: keyof ShardedPhases) => {
        const now = performance.now();
        phases[k] = Math.round(now - tp);
        tp = now;
    };
    await clearOrphanJobs();
    const job = `${JOBS_DIR}/${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const input = `in.${kind === "ifc" ? "ifc" : "step"}`;
    const ws: ShardWorker[] = [];
    try {
        for (let k = 0; k < n; k++) ws.push(await spawn());
        const ok = await Promise.all(ws.map((w) => call(w, w.api.init(kind))));
        if (!ok.every(Boolean)) throw new ShardingUnavailable("this adacpp module or browser cannot shard");
        if (!(await call(ws[0], ws[0].api.canShareReads()))) {
            throw new ShardingUnavailable("this browser has no shared read-only OPFS handles");
        }
        lap("start");

        const src = "bytes" in source ? Comlink.transfer({bytes: source.bytes}, [source.bytes]) : {url: source.url};
        await call(ws[0], ws[0].api.stageInput(job, input, src));
        lap("stage");
        if ((await call(ws[0], ws[0].api.prepare(job, input, n))) < 0) throw new Error("reading the model failed");
        lap("prepare");

        // Every worker opens the input at once (canShareReads checked that the browser allows it).
        let infos: ShardOpenInfo[];
        try {
            infos = await Promise.all(
                ws.map((w, k) => call(w, w.api.open(job, input, k, opts, n * BATCHES_PER_WORKER))),
            );
        } catch (e) {
            throw new ShardingUnavailable(`workers cannot share the input: ${e}`);
        }
        if (infos.some((i) => i.roots < 0)) throw new Error("a shard could not read the index");
        lap("open");

        const sched = new ShardScheduler(n, infos[0].huge, infos[0].ends);
        const total = sched.total;
        let done = 0;
        let wake: () => void = () => undefined;
        let changed = new Promise<void>((r) => (wake = r));
        const notify = () => {
            const w = wake;
            changed = new Promise<void>((r) => (wake = r));
            w();
        };
        const runTask = (w: ShardWorker, t: ShardTask): Promise<unknown> => {
            if (t.kind === "huge") return call(w, w.api.processHuge(t.h, t.f0, t.f1));
            if (t.kind === "assemble") return call(w, w.api.assembleHuge(t.h, t.chunk));
            return call(w, w.api.process(t.begin, t.end));
        };
        await Promise.all(
            ws.map(async (w) => {
                for (;;) {
                    const t = sched.next();
                    if (t === "done") return;
                    if (t === "wait") {
                        await changed;
                        continue;
                    }
                    const r = await runTask(w, t);
                    if (typeof r === "number" && r < 0) throw new Error(`shard task ${t.kind} failed`);
                    sched.complete(t);
                    onProgress?.({done: ++done, total});
                    notify();
                }
            }),
        );

        lap("convert");
        await Promise.all(ws.map((w) => call(w, w.api.persist())));
        // Every worker has let go of the source: drop it before the merge writes the GLB next to the
        // lanes, so the job's OPFS peak is lanes + GLB, not source + lanes + GLB.
        const jobDir = await (await jobsRoot()).getDirectoryHandle(job.slice(JOBS_DIR.length + 1));
        await jobDir.removeEntry(input).catch(() => undefined);
        lap("persist");
        const lanes = await call(ws[0], ws[0].api.merge(n, opts.meshopt));
        if (lanes <= 0) throw new Error("merging the shards failed");
        lap("merge");
        const glb = await readGlb(job.slice(JOBS_DIR.length + 1));
        lap("read");
        const products = infos[0].roots + infos[0].huge.length;
        return {glb, products, ms: performance.now() - t0, workers: n, phases};
    } finally {
        for (const w of ws) w.worker.terminate();
        await removeJob(job);
    }
}
