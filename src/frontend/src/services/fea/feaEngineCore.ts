// The browser FEA engine: load combinations and envelopes materialised by the
// adacpp_fea wasm kernel over base strides cached in the OPFS store.
//
// Runs wherever it is handed a module and a store: the engine worker
// (workers/feaEngine.worker.ts -- OPFS through its own API, Dexie index) and
// the node test that holds it byte-equal to the backend's numpy reference.
//
// STAGING. The kernel reads and writes files in the module's FS. The store's
// files are not there: the adacpp_fea build is single-threaded, and WASMFS's
// OPFS backend needs pthreads or JSPI -- every file operation on an OPFS
// mount traps. So the kernel reads a HEAP MIRROR of each base blob: a file
// in the module's in-heap FS at the base blob's own offsets, holding the
// header and the strides some kernel call has read (copied from the store on
// first use, kept for the next case, LRU-bounded by ``maxMirrorBytes``).
// Every call reads its real step indices from it, and its single-step output
// is copied into the store afterwards. Combinations of one deck share their
// stored cases, so after the first case a combination costs the kernel pass
// and the write-back, not a re-read of its inputs.

import type {FeaCaseOverlay, FeaEnvelope, FeaManifest} from "../api/fea";
import {
    buildCaseOverlay,
    CASE_OVERLAY_NAME,
    caseFields,
    LocalCaseUnsupported,
    planCase,
    planOutputs,
    stringifyJson,
    WASM_ENGINE,
    type CasePlan,
    type KernelJob,
    type KernelStepStats,
} from "./feaCasePlan";
import type {FeaRangeFetcher} from "./feaFetcher";
import type {EmscriptenFsLike, OpfsFeaStore} from "./opfsFeaStore";

/** The adacpp_fea embind surface (adacpp src/fea/fea_wasm.cpp). Every verb
 *  returns a JSON string ``{"ok": true, ...}`` or ``{"ok": false, "error"}``. */
export interface FeaKernelModule {
    FS: EmscriptenFsLike;
    combineField(inPathsJson: string, stepIdx: number | number[], factors: Float32Array, outPath: string, deriveJson: string): string;
    envelopeField(inPathsJson: string, stepIdx: number | number[], outPath: string, govPath: string): string;
    readBlobHeader(path: string): string;
    version(): string;
}

export interface KernelResult {
    ok: boolean;
    error?: string;
    steps?: KernelStepStats[];
    timing_ms?: Record<string, number>;
}

export interface CaseTiming {
    /** Base strides fetched for this case (bytes) and the time spent on it. */
    fetched_bytes: number;
    fetch_ms: number;
    /** Staging + kernel + writing the case into the store (the split below). */
    kernel_ms: number;
    /** Reading strides from the store into the heap. */
    stage_ms?: number;
    /** Inside ``combineField`` (read + combine + derive + write, in the heap). */
    combine_ms?: number;
    /** Copying results into the store. */
    commit_ms?: number;
    total_ms: number;
    jobs: number;
}

export interface MaterialisedCase {
    overlay: FeaCaseOverlay;
    /** False when the store already held the case. */
    computed: boolean;
    timing?: CaseTiming;
}

/** The heap-FS directory kernel calls are staged in (mirrors and outputs). */
export const STAGE_DIR = "/fea-stage";

/** Default bound on the heap mirrors of base blobs. */
export const DEFAULT_MAX_MIRROR_BYTES = 512 * 1024 ** 2;

interface Mirror {
    path: string;
    present: Set<number>;
    /** The heap file's size: the header plus up to the highest stride read. */
    bytes: number;
}

/** An envelope stages every case's stride of a bucket at once; above this it
 *  is left to the server. */
export const MAX_ENVELOPE_STAGE_BYTES = 1.5 * 1024 ** 3;

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

function parseResult(raw: string, what: string): KernelResult {
    let res: KernelResult;
    try {
        res = JSON.parse(raw) as KernelResult;
    } catch {
        throw new Error(`${what}: the kernel returned no JSON`);
    }
    if (!res.ok) throw new Error(`${what}: ${res.error ?? "kernel error"}`);
    return res;
}

export function bakeVersionOf(manifest: Pick<FeaManifest, "bake_version">): number {
    return Number(manifest.bake_version ?? 0);
}

export class FeaEngineCore {
    readonly mod: FeaKernelModule;
    readonly store: OpfsFeaStore;
    readonly maxMirrorBytes: number;
    private readonly locks = new Map<string, Promise<unknown>>();
    private readonly mirrors = new Map<string, Mirror>();
    private mirrorBytes = 0;
    private stageSeq = 0;

    constructor(mod: FeaKernelModule, store: OpfsFeaStore, opts: {maxMirrorBytes?: number} = {}) {
        this.mod = mod;
        this.store = store;
        this.maxMirrorBytes = opts.maxMirrorBytes ?? DEFAULT_MAX_MIRROR_BYTES;
    }

    /** Bytes the heap mirrors hold (diagnostics). */
    mirroredBytes(): number {
        return this.mirrorBytes;
    }

    version(): string {
        try {
            return this.mod.version();
        } catch {
            return "unknown";
        }
    }

    /** One materialisation per key at a time (concurrent asks share it). */
    private once<T>(key: string, fn: () => Promise<T>): Promise<T> {
        const cur = this.locks.get(key) as Promise<T> | undefined;
        if (cur) return cur;
        const p = fn().finally(() => this.locks.delete(key));
        this.locks.set(key, p);
        return p;
    }

    // ── staging ─────────────────────────────────────────────────────────────

    private stagePath(tag: string): string {
        const FS = this.mod.FS;
        FS.mkdirTree(STAGE_DIR);
        return `${STAGE_DIR}/${++this.stageSeq}-${tag}.bin`;
    }

    private unlink(...paths: string[]): void {
        for (const p of paths) {
            try {
                this.mod.FS.unlink(p);
            } catch {
                /* already gone */
            }
        }
    }

    /** The heap mirror of base blob ``url`` with ``steps`` present (copied
     *  from the store on first use). Returns its path. */
    private async mirror(sourceId: string, bv: number, url: string, h: number, s: number, steps: number[]) {
        const key = `${sourceId}/b${bv}/${url}`;
        let m = this.mirrors.get(key);
        const FS = this.mod.FS;
        if (m) {
            this.mirrors.delete(key); // most recently used last
        } else {
            m = {path: this.stagePath("mirror"), present: new Set(), bytes: 0};
            FS.writeFile(m.path, await this.store.readBase(sourceId, bv, url, 0, h));
            m.bytes = h;
            this.mirrorBytes += h;
        }
        this.mirrors.set(key, m);
        const missing = [...new Set(steps)].filter((st) => !m!.present.has(st)).sort((a, b) => a - b);
        if (missing.length) {
            const stream = FS.open(m.path, "r+");
            try {
                for (const st of missing) {
                    const stride = await this.store.readBase(sourceId, bv, url, h + st * s, s);
                    FS.write(stream, stride, 0, stride.byteLength, h + st * s);
                    m.present.add(st);
                }
            } finally {
                FS.close(stream);
            }
            const size = h + (Math.max(...m.present) + 1) * s;
            this.mirrorBytes += size - m.bytes;
            m.bytes = size;
        }
        // Keep the mirrors within budget; never drop the one just asked for.
        for (const [k, other] of this.mirrors) {
            if (this.mirrorBytes <= this.maxMirrorBytes || k === key) break;
            this.unlink(other.path);
            this.mirrors.delete(k);
            this.mirrorBytes -= other.bytes;
        }
        return m.path;
    }

    /** Drop every heap mirror (e.g. after the store was cleared). */
    dropMirrors(): void {
        for (const m of this.mirrors.values()) this.unlink(m.path);
        this.mirrors.clear();
        this.mirrorBytes = 0;
    }

    /** Run one combine job; its output is left at the returned heap path. */
    private async runJob(
        sourceId: string,
        bv: number,
        manifest: FeaManifest,
        job: KernelJob,
        what: string,
        split: Split = newSplit(),
    ) {
        const {h, s} = inputLayout(manifest, job);
        const t0 = now();
        const input = await this.mirror(sourceId, bv, job.inUrl, h, s, job.steps);
        const out = this.stagePath("out");
        const t1 = now();
        split.stage += t1 - t0;
        try {
            const res = parseResult(
                this.mod.combineField(
                    JSON.stringify(job.steps.map(() => input)),
                    job.steps,
                    new Float32Array(job.factors),
                    out,
                    JSON.stringify(job.derive),
                ),
                what,
            );
            split.combine += now() - t1;
            if (!res.steps?.[0]) throw new Error(`${what}: the kernel reported no step stats`);
            return {out, stats: res.steps[0]};
        } catch (err) {
            this.unlink(out);
            throw err;
        }
    }

    /** Move a staged heap file into the store. */
    private async commit(heapPath: string, storePath: string, split: Split = newSplit()): Promise<void> {
        const t0 = now();
        const bytes = this.mod.FS.readFile(heapPath);
        this.unlink(heapPath);
        await this.store.fs.writeFile(storePath, bytes);
        split.commit += now() - t0;
    }

    // ── cases ───────────────────────────────────────────────────────────────

    /** Fetch (into the store) the base strides ``fields`` need for ``caseNs``
     *  (default: every Tier-A combination) -- a warm-up; materialising a case
     *  fetches what it needs by itself. Returns the bytes fetched. */
    async ensureBase(
        sourceId: string,
        manifest: FeaManifest,
        fetch: FeaRangeFetcher,
        opts: {fields?: string[]; caseNs?: number[]} = {},
    ): Promise<number> {
        const bv = bakeVersionOf(manifest);
        const steps = (manifest.combination_steps ?? []).filter(
            (c) => !c.needs_raw && (!opts.caseNs || opts.caseNs.includes(c.n)),
        );
        let fetched = 0;
        for (const step of steps) {
            for (const need of planCase(manifest, step, opts.fields).needs) {
                fetched += await this.store.ensureStrides(sourceId, bv, need, fetch);
            }
        }
        return fetched;
    }

    /** The case's overlay, materialising it (every case field) when the store
     *  does not hold it yet. Throws ``LocalCaseUnsupported`` for a recipe only
     *  the server can do. */
    async materialiseCase(args: {
        sourceId: string;
        sourceKey: string;
        manifest: FeaManifest;
        caseN: number;
        fetch: FeaRangeFetcher;
    }): Promise<MaterialisedCase> {
        const step = (args.manifest.combination_steps ?? []).find((c) => c.n === args.caseN);
        if (!step) throw new LocalCaseUnsupported(`case ${args.caseN} is not a lazy combination`);
        const plan = planCase(args.manifest, step);
        return this.once(`case:${args.sourceId}:${plan.caseDir}`, () => this.runCase(args, plan));
    }

    private async runCase(
        args: {sourceId: string; sourceKey: string; manifest: FeaManifest; fetch: FeaRangeFetcher},
        plan: CasePlan,
    ): Promise<MaterialisedCase> {
        const {sourceId, sourceKey, manifest, fetch} = args;
        const bv = bakeVersionOf(manifest);
        const fs = this.store.fs;
        const dir = this.store.caseDir(sourceId, bv, plan.caseDir);
        const prefix = `_derived/${sourceKey.replace(/^\/+/, "")}.fea/cases/${plan.caseDir}/`;
        if (await this.store.hasCase(sourceId, bv, plan.caseDir)) {
            const p = `${dir}/${CASE_OVERLAY_NAME}`;
            const overlay = JSON.parse(new TextDecoder().decode(await fs.read(p, 0, await fs.size(p)))) as FeaCaseOverlay;
            if (overlay.case?.recipe_hash === plan.step.recipe_hash) return {overlay: {...overlay, prefix}, computed: false};
        }
        const t0 = now();
        let fetched = 0;
        for (const need of plan.needs) fetched += await this.store.ensureStrides(sourceId, bv, need, fetch);
        const t1 = now();
        const split = newSplit();
        const outBytes = plan.jobs.reduce((a, j) => a + strideOf(manifest, j.field, j.elemType) + 1024, 0);
        await this.store.reserve(outBytes);
        await fs.remove(dir);
        await fs.mkdirTree(dir);
        const stats = new Map<string, KernelStepStats>();
        try {
            for (const job of plan.jobs) {
                const what = `${job.field}${job.elemType ? `/${job.elemType}` : ""}`;
                const res = await this.runJob(sourceId, bv, manifest, job, what, split);
                await this.commit(res.out, `${dir}/${job.outUrl}`, split);
                stats.set(job.outUrl, res.stats);
            }
            const producer = {engine: WASM_ENGINE, version: this.version(), tier: "A"};
            const overlay = buildCaseOverlay(manifest, plan, stats, producer, prefix);
            // Written last: a reader that sees fea.case.json sees a complete case.
            const {prefix: _prefix, ...stored} = overlay;
            await fs.writeFile(`${dir}/${CASE_OVERLAY_NAME}`, new TextEncoder().encode(stringifyJson(stored)));
            await this.store.recordCase(sourceId, bv, plan.caseDir, planOutputs(plan), producer);
            const t2 = now();
            return {
                overlay,
                computed: true,
                timing: {
                    fetched_bytes: fetched,
                    fetch_ms: t1 - t0,
                    kernel_ms: t2 - t1,
                    stage_ms: split.stage,
                    combine_ms: split.combine,
                    commit_ms: split.commit,
                    total_ms: t2 - t0,
                    jobs: plan.jobs.length,
                },
            };
        } catch (err) {
            await fs.remove(dir);
            throw err;
        }
    }

    // ── envelopes ───────────────────────────────────────────────────────────

    /** ``field``'s envelope over every Tier-A combination: max / min blobs (two
     *  steps, the base filename) + governing-case sidecars, and
     *  ``fea.envelope.json`` -- ``combine.write_envelope``'s layout. */
    async envelope(args: {sourceId: string; manifest: FeaManifest; field: string; fetch: FeaRangeFetcher}): Promise<FeaEnvelope> {
        return this.once(`env:${args.sourceId}:${args.field}`, () => this.runEnvelope(args));
    }

    private async runEnvelope(args: {
        sourceId: string;
        manifest: FeaManifest;
        field: string;
        fetch: FeaRangeFetcher;
    }): Promise<FeaEnvelope> {
        const {sourceId, manifest, field, fetch} = args;
        const bv = bakeVersionOf(manifest);
        const fs = this.store.fs;
        const dir = this.store.envelopeDir(sourceId, bv, field);
        const docPath = `${dir}/fea.envelope.json`;
        if (await this.store.hasEnvelope(sourceId, bv, field)) {
            return JSON.parse(new TextDecoder().decode(await fs.read(docPath, 0, await fs.size(docPath))));
        }
        if (!caseFields(manifest).includes(field)) throw new LocalCaseUnsupported(`no case field ${field}`);
        const all = manifest.combination_steps ?? [];
        const chosen = all.filter((c) => !c.needs_raw);
        if (!chosen.length) throw new LocalCaseUnsupported("no combination can be superposed here");
        if (chosen.length > 0xffff) throw new LocalCaseUnsupported("too many cases for a uint16 governing index");
        const fieldMeta = manifest.fields.find((f) => f.name_canonical === field)!;
        const buckets = fieldMeta.per_type?.length
            ? fieldMeta.per_type.map((pt) => ({elem_type: pt.elem_type as string | null, blob: pt.blob}))
            : [{elem_type: null as string | null, blob: fieldMeta.blob!}];
        const biggest = Math.max(...buckets.map((b) => b.blob.stride_bytes));
        if (chosen.length * biggest > MAX_ENVELOPE_STAGE_BYTES) {
            throw new LocalCaseUnsupported(`envelope of ${field}: ${chosen.length} cases do not fit the staging heap`);
        }
        const plans = chosen.map((step) => planCase(manifest, step, [field]));
        for (const plan of plans) for (const need of plan.needs) await this.store.ensureStrides(sourceId, bv, need, fetch);
        await fs.remove(dir);
        await fs.mkdirTree(dir);
        const staged: string[] = [];
        try {
            const blobs: unknown[] = [];
            const range: Record<string, [number, number]> = {};
            const files: string[] = [];
            for (const b of buckets) {
                // Every case's stride of this bucket, staged; then one envelope pass.
                const perCase: string[] = [];
                for (const plan of plans) {
                    const job = plan.jobs.find((j) => j.elemType === b.elem_type)!;
                    const res = await this.runJob(sourceId, bv, manifest, job, `envelope ${field} case ${plan.step.n}`);
                    staged.push(res.out);
                    perCase.push(res.out);
                }
                const out = this.stagePath("env");
                const gov = this.stagePath("gov");
                staged.push(out, gov);
                const res = parseResult(this.mod.envelopeField(JSON.stringify(perCase), 0, out, gov), `envelope ${field}`);
                this.unlink(...perCase);
                const govName = `${b.blob.url}.governing.bin`;
                await this.commit(out, `${dir}/${b.blob.url}`);
                await this.commit(gov, `${dir}/${govName}`);
                files.push(b.blob.url, govName);
                const [hi, lo] = [res.steps?.[0], res.steps?.[1]];
                fieldMeta.components.forEach((c, k) => {
                    const top = hi?.scalar_range_per_component[k]?.[1];
                    const bottom = lo?.scalar_range_per_component[k]?.[0];
                    if (top === null || top === undefined || bottom === null || bottom === undefined) return;
                    const cur = range[c];
                    range[c] = cur ? [Math.min(cur[0], bottom), Math.max(cur[1], top)] : [bottom, top];
                });
                blobs.push({elem_type: b.elem_type, blob: {...b.blob}, steps: ["max", "min"], governing_url: govName});
            }
            const covered = chosen.map((c) => c.n);
            const doc = {
                version: 1,
                kind: "fea_envelope" as const,
                bake_version: manifest.bake_version ?? 4,
                src: manifest.src ?? "",
                field,
                components: [...fieldMeta.components],
                cases: covered,
                skipped: all.map((c) => c.n).filter((n) => !covered.includes(n)),
                scalar_range: range,
                blobs,
                producer: {engine: WASM_ENGINE, version: this.version(), tier: "A"},
            };
            await fs.writeFile(docPath, new TextEncoder().encode(stringifyJson(doc)));
            await this.store.recordEnvelope(sourceId, bv, field, [...files, "fea.envelope.json"]);
            return doc as FeaEnvelope;
        } catch (err) {
            await fs.remove(dir);
            throw err;
        } finally {
            this.unlink(...staged);
        }
    }

    readFile(sourceId: string, bakeVersion: number, rel: string, range?: {start: number; end: number}) {
        return this.store.readFile(sourceId, bakeVersion, rel, range);
    }
}

interface Split {
    stage: number;
    combine: number;
    commit: number;
}

const newSplit = (): Split => ({stage: 0, combine: 0, commit: 0});

function inputLayout(manifest: FeaManifest, job: KernelJob): {h: number; s: number} {
    for (const f of manifest.fields) {
        const blobs = f.per_type?.length ? f.per_type.map((p) => p.blob) : f.blob ? [f.blob] : [];
        const b = blobs.find((x) => x.url === job.inUrl);
        if (b) return {h: b.header_bytes, s: b.stride_bytes};
    }
    throw new Error(`no base blob ${job.inUrl} in the manifest`);
}

function strideOf(manifest: FeaManifest, field: string, elemType: string | null): number {
    const f = manifest.fields.find((x) => x.name_canonical === field);
    if (!f) return 0;
    if (elemType === null) return f.blob?.stride_bytes ?? 0;
    return f.per_type?.find((p) => p.elem_type === elemType)?.blob.stride_bytes ?? 0;
}
