// The browser's FEA artefact cache: base strides, materialised cases and
// envelopes in the Origin Private File System, indexed in IndexedDB.
//
// LAYOUT (OPFS root):
//
//   /adapy-fea/v1/<sourceId>/b<bake_version>/base/fea.<field>[.<etype>.elements].bin
//   /adapy-fea/v1/<sourceId>/b<bake_version>/cases/<n>-<hash8>/{fea.*.bin, fea.case.json}
//   /adapy-fea/v1/<sourceId>/b<bake_version>/envelopes/<field>/{fea.*.bin, *.governing.bin, fea.envelope.json}
//
// BASE BLOBS ARE SPARSE. A base blob is the server's file at the same offsets,
// but only the strides something asked for are present: the 1 KB header plus,
// per stored case, ``header + i*stride .. + stride``. A combination of three
// stored cases fetches three strides of each blob it touches, not the bake.
// Which strides are present is the index's job (``steps``, -1 = the header);
// a read of a range not fully present returns null and the caller asks the
// server instead -- the store never hands out the zeros of a hole.
//
// SINGLE OWNER. Every byte goes through one ``FeaFs``, owned by the engine
// worker: in the browser ``opfsNativeFeaFs`` (the OPFS API's synchronous
// access handles, which are exclusive -- so nothing else may hold them). The
// kernel does NOT read these files itself: the adacpp_fea build's WASMFS OPFS
// backend needs pthreads or JSPI, and in the single-threaded module every
// file operation on an OPFS mount traps. The engine stages the strides a
// call needs into the module's in-heap FS instead (feaEngineCore.ts).
//
// EVICTION. LRU over index entries, cases and envelopes before base blobs,
// pinned entries never. Run before a write that would not fit the budget
// (the smaller of ``maxBytes`` and half the origin's free quota).
//
// The fs / index / clock / storage-manager are injected, so this runs under
// node with an in-memory fs and index (tests) and in the worker with OPFS and
// Dexie.

import Dexie from "dexie";

import type {BaseBlobNeed} from "./feaCasePlan";
import type {FeaRangeFetcher} from "./feaFetcher";

export const FEA_STORE_ROOT = "/adapy-fea/v1";
export const FEA_STORE_DB = "FeaStoreDB";

export type FeaStoreKind = "base" | "case" | "envelope";

export interface FeaStoreRecord {
    /** ``<sourceId>/b<v>/<kind>/<name>`` -- unique. */
    id: string;
    sourceId: string;
    kind: FeaStoreKind;
    /** base: the blob filename; case: ``<n>-<hash8>``; envelope: the field's directory name. */
    name: string;
    /** Bytes on disk (a sparse base blob: its logical size). */
    bytes: number;
    lastAccess: number;
    pinned: boolean;
    producer?: {engine: string; version?: string; tier?: string};
    /** base only: the strides present (-1 = the header), and the layout. */
    steps?: number[];
    header_bytes?: number;
    stride_bytes?: number;
    /** base only: the whole file is present (the server ignored a Range). */
    complete?: boolean;
}

/** The file operations the store needs. Paths are absolute ``/a/b/c``. */
export interface FeaFs {
    mkdirTree(path: string): Promise<void>;
    /** Bytes, or -1 when the file does not exist. */
    size(path: string): Promise<number>;
    /** Write ``bytes`` at ``offset``, creating the file (and growing it) as needed. */
    writeAt(path: string, offset: number, bytes: Uint8Array): Promise<void>;
    /** Replace the file's content. */
    writeFile(path: string, bytes: Uint8Array): Promise<void>;
    read(path: string, start: number, length: number): Promise<Uint8Array>;
    /** Remove a file or a directory tree; missing is not an error. */
    remove(path: string): Promise<void>;
}

export interface FeaIndex {
    get(id: string): Promise<FeaStoreRecord | undefined>;
    put(rec: FeaStoreRecord): Promise<void>;
    delete(id: string): Promise<void>;
    all(): Promise<FeaStoreRecord[]>;
}

export interface FeaStoreOptions {
    fs: FeaFs;
    index: FeaIndex;
    /** Where the store lives in ``fs``. */
    root?: string;
    now?: () => number;
    /** ``navigator.storage.estimate`` (absent: no quota signal). */
    estimate?: () => Promise<{usage?: number; quota?: number}>;
    /** ``navigator.storage.persist`` -- asked once, on the first write. */
    persist?: () => Promise<boolean>;
    /** Upper bound on what the store keeps, whatever the quota says. */
    maxBytes?: number;
    /** An entry used again within this long keeps its ``lastAccess`` (an LRU
     *  needs minutes, not milliseconds, and every touch is an index write). */
    touchIntervalMs?: number;
}

export interface FeaStoreUsage {
    total: number;
    byKind: Record<FeaStoreKind, number>;
    sources: Array<{sourceId: string; bytes: number; lastAccess: number}>;
    quota?: number;
    usage?: number;
    persisted?: boolean;
}

const DEFAULT_MAX_BYTES = 8 * 1024 ** 3;

export class OpfsFeaStore {
    readonly fs: FeaFs;
    readonly index: FeaIndex;
    readonly root: string;
    private readonly now: () => number;
    private readonly estimate?: FeaStoreOptions["estimate"];
    private readonly persistFn?: FeaStoreOptions["persist"];
    private readonly maxBytes: number;
    private readonly touchIntervalMs: number;
    private persistAsked = false;
    private persisted: boolean | undefined;

    constructor(opts: FeaStoreOptions) {
        this.fs = opts.fs;
        this.index = opts.index;
        this.root = (opts.root ?? FEA_STORE_ROOT).replace(/\/+$/, "");
        this.now = opts.now ?? (() => Date.now());
        this.estimate = opts.estimate;
        this.persistFn = opts.persist;
        this.maxBytes = opts.maxBytes ?? DEFAULT_MAX_BYTES;
        this.touchIntervalMs = opts.touchIntervalMs ?? 30_000;
    }

    // ── layout ──────────────────────────────────────────────────────────────

    dir(sourceId: string, bakeVersion: number): string {
        return `${this.root}/${cleanName(sourceId)}/b${bakeVersion}`;
    }
    basePath(sourceId: string, bv: number, url: string): string {
        return `${this.dir(sourceId, bv)}/base/${cleanName(url)}`;
    }
    caseDir(sourceId: string, bv: number, caseDir: string): string {
        return `${this.dir(sourceId, bv)}/cases/${cleanName(caseDir)}`;
    }
    envelopeDir(sourceId: string, bv: number, field: string): string {
        return `${this.dir(sourceId, bv)}/envelopes/${envelopeDirName(field)}`;
    }
    private id(sourceId: string, bv: number, kind: FeaStoreKind, name: string): string {
        return `${sourceId}/b${bv}/${kind}/${name}`;
    }

    // ── base strides ────────────────────────────────────────────────────────

    /** Make every stride ``need`` lists present in the base blob, fetching
     *  only those missing. Returns the bytes fetched. */
    async ensureStrides(sourceId: string, bv: number, need: BaseBlobNeed, fetch: FeaRangeFetcher): Promise<number> {
        const id = this.id(sourceId, bv, "base", need.url);
        const path = this.basePath(sourceId, bv, need.url);
        let rec = await this.index.get(id);
        if (rec && ((await this.fs.size(path)) < 0 || rec.stride_bytes !== need.stride_bytes)) {
            // The file went away (cleared site data) or the layout changed: start over.
            await this.fs.remove(path);
            await this.index.delete(id);
            rec = undefined;
        }
        if (rec?.complete) {
            await this.touchRecord(rec);
            return 0;
        }
        const present = new Set(rec?.steps ?? []);
        // In file order: sequential ranges are kinder to every storage backend.
        const missing = [...new Set(need.steps)].filter((s) => !present.has(s)).sort((a, b) => a - b);
        const wantHeader = !present.has(-1);
        if (!missing.length && !wantHeader) {
            await this.touchRecord(rec!);
            return 0;
        }
        const h = need.header_bytes;
        const s = need.stride_bytes;
        await this.makeRoom(missing.length * s + (wantHeader ? h : 0));
        await this.fs.mkdirTree(parentDir(path));
        let fetched = 0;
        let complete = false;
        const ranges: Array<[number, number, number]> = []; // [stepTag, start, end]
        if (wantHeader) ranges.push([-1, 0, h - 1]);
        for (const st of missing) ranges.push([st, h + st * s, h + (st + 1) * s - 1]);
        for (const [tag, start, end] of ranges) {
            const {buf, ranged} = await fetch(need.url, start, end);
            const bytes = new Uint8Array(buf);
            fetched += bytes.byteLength;
            if (!ranged) {
                // The whole object came back: keep all of it, every step is present.
                await this.fs.writeFile(path, bytes);
                complete = true;
                break;
            }
            if (bytes.byteLength !== end - start + 1) {
                throw new Error(`${need.url}: short range ${start}-${end} (${bytes.byteLength} bytes)`);
            }
            await this.fs.writeAt(path, start, bytes);
            present.add(tag);
        }
        await this.index.put({
            id,
            sourceId,
            kind: "base",
            name: need.url,
            bytes: Math.max(0, await this.fs.size(path)),
            lastAccess: this.now(),
            pinned: rec?.pinned ?? false,
            steps: complete ? [] : [...present].sort((a, b) => a - b),
            header_bytes: h,
            stride_bytes: s,
            ...(complete ? {complete: true} : {}),
        });
        return fetched;
    }

    /** Bytes of a base blob the store holds, for the engine right after
     *  ``ensureStrides`` made them present (no presence check, no index
     *  write: the kernel's staging reads many strides per case). */
    async readBase(sourceId: string, bv: number, url: string, start: number, length: number): Promise<Uint8Array> {
        const bytes = await this.fs.read(this.basePath(sourceId, bv, url), start, length);
        if (bytes.byteLength !== length) throw new Error(`${url}: ${start}+${length} is not in the store`);
        return bytes;
    }

    // ── cases / envelopes ───────────────────────────────────────────────────

    async hasCase(sourceId: string, bv: number, caseDir: string): Promise<boolean> {
        const rec = await this.index.get(this.id(sourceId, bv, "case", caseDir));
        if (!rec) return false;
        if ((await this.fs.size(`${this.caseDir(sourceId, bv, caseDir)}/fea.case.json`)) < 0) {
            await this.index.delete(rec.id);
            return false;
        }
        await this.touchRecord(rec);
        return true;
    }

    async recordCase(
        sourceId: string,
        bv: number,
        caseDir: string,
        files: string[],
        producer?: FeaStoreRecord["producer"],
    ): Promise<void> {
        const dir = this.caseDir(sourceId, bv, caseDir);
        let bytes = 0;
        for (const f of files) bytes += Math.max(0, await this.fs.size(`${dir}/${f}`));
        await this.index.put({
            id: this.id(sourceId, bv, "case", caseDir),
            sourceId,
            kind: "case",
            name: caseDir,
            bytes,
            lastAccess: this.now(),
            pinned: false,
            ...(producer ? {producer} : {}),
        });
    }

    async hasEnvelope(sourceId: string, bv: number, field: string): Promise<boolean> {
        const rec = await this.index.get(this.id(sourceId, bv, "envelope", envelopeDirName(field)));
        if (!rec) return false;
        if ((await this.fs.size(`${this.envelopeDir(sourceId, bv, field)}/fea.envelope.json`)) < 0) {
            await this.index.delete(rec.id);
            return false;
        }
        await this.touchRecord(rec);
        return true;
    }

    async recordEnvelope(sourceId: string, bv: number, field: string, files: string[]): Promise<void> {
        const dir = this.envelopeDir(sourceId, bv, field);
        let bytes = 0;
        for (const f of files) bytes += Math.max(0, await this.fs.size(`${dir}/${f}`));
        await this.index.put({
            id: this.id(sourceId, bv, "envelope", envelopeDirName(field)),
            sourceId,
            kind: "envelope",
            name: envelopeDirName(field),
            bytes,
            lastAccess: this.now(),
            pinned: false,
        });
    }

    // ── reads ───────────────────────────────────────────────────────────────

    /** Bytes of a file under the source's tree (``base/<f>``, ``cases/<d>/<f>``,
     *  ``envelopes/<dir>/<f>``), whole or ``[start, end]`` inclusive; null
     *  when it (or, for a sparse base blob, that range) is not present. */
    async readFile(
        sourceId: string,
        bv: number,
        rel: string,
        range?: {start: number; end: number},
    ): Promise<Uint8Array | null> {
        const clean = rel.replace(/^\/+/, "");
        if (clean.split("/").some((p) => p === ".." || p === "." || p === "")) return null;
        const path = `${this.dir(sourceId, bv)}/${clean}`;
        const size = await this.fs.size(path);
        if (size < 0) return null;
        const [kind, ...rest] = clean.split("/");
        if (kind === "base") {
            const rec = await this.index.get(this.id(sourceId, bv, "base", rest.join("/")));
            if (!rec) return null;
            if (!rec.complete && !baseRangePresent(rec, range ?? {start: 0, end: size - 1})) return null;
            await this.touchRecord(rec);
        } else if (kind === "cases" || kind === "envelopes") {
            const rec = await this.index.get(this.id(sourceId, bv, kind === "cases" ? "case" : "envelope", rest[0]));
            if (rec) await this.touchRecord(rec);
        }
        if (!range) return this.fs.read(path, 0, size);
        const end = Math.min(range.end, size - 1);
        if (range.start > end) return new Uint8Array(0);
        return this.fs.read(path, range.start, end - range.start + 1);
    }

    // ── accounting ──────────────────────────────────────────────────────────

    async usage(): Promise<FeaStoreUsage> {
        const all = await this.index.all();
        const byKind: Record<FeaStoreKind, number> = {base: 0, case: 0, envelope: 0};
        const bySource = new Map<string, {bytes: number; lastAccess: number}>();
        let total = 0;
        for (const r of all) {
            total += r.bytes;
            byKind[r.kind] += r.bytes;
            const s = bySource.get(r.sourceId) ?? {bytes: 0, lastAccess: 0};
            s.bytes += r.bytes;
            s.lastAccess = Math.max(s.lastAccess, r.lastAccess);
            bySource.set(r.sourceId, s);
        }
        let quota: number | undefined;
        let usage: number | undefined;
        try {
            const est = await this.estimate?.();
            quota = est?.quota;
            usage = est?.usage;
        } catch {
            /* no quota signal */
        }
        return {
            total,
            byKind,
            sources: [...bySource].map(([sourceId, v]) => ({sourceId, ...v})),
            ...(quota !== undefined ? {quota} : {}),
            ...(usage !== undefined ? {usage} : {}),
            ...(this.persisted !== undefined ? {persisted: this.persisted} : {}),
        };
    }

    /** The budget the store keeps itself under: ``maxBytes``, or less when the
     *  origin's quota is tight (half of what is free to the origin plus what
     *  the store already holds). */
    async budget(): Promise<number> {
        let budget = this.maxBytes;
        try {
            const est = await this.estimate?.();
            if (est?.quota !== undefined) {
                const held = (await this.index.all()).reduce((a, r) => a + r.bytes, 0);
                const free = Math.max(0, est.quota - (est.usage ?? 0));
                budget = Math.min(budget, held + free / 2);
            }
        } catch {
            /* keep maxBytes */
        }
        return budget;
    }

    /** Evict least-recently-used entries until ``needBytes`` more fit the
     *  budget (or ``targetBytes``, when given, is reached). Cases and
     *  envelopes go first, base blobs only after them; pinned never.
     *  Returns the ids evicted. */
    async evict(opts: {needBytes?: number; targetBytes?: number} = {}): Promise<string[]> {
        const all = await this.index.all();
        let total = all.reduce((a, r) => a + r.bytes, 0);
        const limit = opts.targetBytes ?? (await this.budget()) - (opts.needBytes ?? 0);
        if (total <= limit) return [];
        const order = all
            .filter((r) => !r.pinned)
            .sort((a, b) => kindRank(a.kind) - kindRank(b.kind) || a.lastAccess - b.lastAccess);
        const evicted: string[] = [];
        for (const r of order) {
            if (total <= limit) break;
            await this.removeRecordFiles(r);
            await this.index.delete(r.id);
            total -= r.bytes;
            evicted.push(r.id);
        }
        return evicted;
    }

    /** Forget everything of one source (or, without one, the whole store). */
    async clear(sourceId?: string): Promise<void> {
        for (const r of await this.index.all()) {
            if (sourceId && r.sourceId !== sourceId) continue;
            await this.index.delete(r.id);
        }
        await this.fs.remove(sourceId ? `${this.root}/${cleanName(sourceId)}` : this.root);
    }

    async setPinned(sourceId: string, pinned: boolean): Promise<void> {
        for (const r of await this.index.all()) {
            if (r.sourceId === sourceId) await this.index.put({...r, pinned});
        }
    }

    /** Ask the browser to keep the store (``navigator.storage.persist``), once. */
    async ensurePersisted(): Promise<boolean | undefined> {
        if (this.persistAsked) return this.persisted;
        this.persistAsked = true;
        try {
            this.persisted = await this.persistFn?.();
        } catch {
            this.persisted = false;
        }
        return this.persisted;
    }

    /** Make room before writing a case or an envelope of about ``bytes``. */
    async reserve(bytes: number): Promise<void> {
        await this.makeRoom(bytes);
    }

    private async makeRoom(needBytes: number): Promise<void> {
        await this.ensurePersisted();
        await this.evict({needBytes});
    }

    private async touchRecord(rec: FeaStoreRecord): Promise<void> {
        const t = this.now();
        if (this.touchIntervalMs > 0 && t - rec.lastAccess < this.touchIntervalMs) return;
        await this.index.put({...rec, lastAccess: t});
    }

    private async removeRecordFiles(r: FeaStoreRecord): Promise<void> {
        const bv = Number(/\/b(\d+)\//.exec(r.id)?.[1] ?? 0);
        if (r.kind === "base") await this.fs.remove(this.basePath(r.sourceId, bv, r.name));
        else if (r.kind === "case") await this.fs.remove(this.caseDir(r.sourceId, bv, r.name));
        else await this.fs.remove(this.envelopeDir(r.sourceId, bv, r.name));
    }
}

function kindRank(kind: FeaStoreKind): number {
    return kind === "base" ? 1 : 0;
}

function baseRangePresent(rec: FeaStoreRecord, range: {start: number; end: number}): boolean {
    const h = rec.header_bytes ?? 0;
    const s = rec.stride_bytes ?? 0;
    const present = new Set(rec.steps ?? []);
    let pos = range.start;
    while (pos <= range.end) {
        if (pos < h) {
            if (!present.has(-1)) return false;
            pos = h;
            continue;
        }
        if (s <= 0) return false;
        const step = Math.floor((pos - h) / s);
        if (!present.has(step)) return false;
        pos = h + (step + 1) * s;
    }
    return true;
}

function parentDir(path: string): string {
    return path.slice(0, path.lastIndexOf("/")) || "/";
}

/** A manifest-relative filename made safe as a path (no traversal, no
 *  leading slash, no empty segments). */
function cleanName(name: string): string {
    return name
        .replace(/^\/+/, "")
        .split("/")
        .filter((p) => p && p !== "." && p !== "..")
        .join("/");
}

/** The backend's ``_envelope_dir``: a field name as one directory name. */
export function envelopeDirName(field: string): string {
    return [...field].map((c) => (/[A-Za-z0-9\-_.]/.test(c) ? c : "_")).join("");
}

// ── implementations ─────────────────────────────────────────────────────────

/** The subset of an emscripten (WASMFS) ``FS`` the engine uses. */
export interface EmscriptenFsLike {
    mkdirTree(path: string, mode?: number): void;
    stat(path: string): {size: number; mode: number};
    open(path: string, flags: string): unknown;
    write(stream: unknown, buffer: Uint8Array, offset: number, length: number, position: number): number;
    read(stream: unknown, buffer: Uint8Array, offset: number, length: number, position: number): number;
    close(stream: unknown): void;
    writeFile(path: string, data: Uint8Array): void;
    readFile(path: string): Uint8Array;
    readdir(path: string): string[];
    unlink(path: string): void;
    rmdir(path: string): void;
}

const S_IFDIR = 0o040000;
const S_IFMT = 0o170000;

/** ``FeaFs`` over a module's in-heap FS (node tests; the worker's staging area). */
export function emscriptenFeaFs(FS: EmscriptenFsLike): FeaFs {
    const isDir = (mode: number) => (mode & S_IFMT) === S_IFDIR;
    const stat = (path: string) => {
        try {
            return FS.stat(path);
        } catch {
            return null;
        }
    };
    const remove = (path: string): void => {
        const st = stat(path);
        if (!st) return;
        if (!isDir(st.mode)) {
            FS.unlink(path);
            return;
        }
        for (const name of FS.readdir(path)) {
            if (name === "." || name === "..") continue;
            remove(`${path}/${name}`);
        }
        FS.rmdir(path);
    };
    return {
        mkdirTree: async (path) => FS.mkdirTree(path),
        size: async (path) => {
            const st = stat(path);
            return st && !isDir(st.mode) ? st.size : -1;
        },
        async writeAt(path, offset, bytes) {
            const stream = FS.open(path, stat(path) ? "r+" : "w+");
            try {
                let done = 0;
                while (done < bytes.byteLength) {
                    done += FS.write(stream, bytes, done, bytes.byteLength - done, offset + done);
                }
            } finally {
                FS.close(stream);
            }
        },
        writeFile: async (path, bytes) => FS.writeFile(path, bytes),
        async read(path, start, length) {
            const out = new Uint8Array(length);
            const stream = FS.open(path, "r");
            try {
                let done = 0;
                while (done < length) {
                    const n = FS.read(stream, out, done, length - done, start + done);
                    if (n <= 0) break;
                    done += n;
                }
                return done === length ? out : out.subarray(0, done);
            } finally {
                FS.close(stream);
            }
        },
        remove: async (path) => remove(path),
    };
}

/** The OPFS handles the native ``FeaFs`` uses (a structural subset of the DOM
 *  types, so a test can stand in for them). */
export interface OpfsSyncHandle {
    read(buffer: Uint8Array, opts?: {at?: number}): number;
    write(buffer: Uint8Array, opts?: {at?: number}): number;
    truncate(size: number): void;
    getSize(): number;
    flush(): void;
    close(): void;
}
export interface OpfsFileHandleLike {
    createSyncAccessHandle(): Promise<OpfsSyncHandle>;
}
export interface OpfsDirHandleLike {
    getDirectoryHandle(name: string, opts?: {create?: boolean}): Promise<OpfsDirHandleLike>;
    getFileHandle(name: string, opts?: {create?: boolean}): Promise<OpfsFileHandleLike>;
    removeEntry(name: string, opts?: {recursive?: boolean}): Promise<void>;
}

/** ``FeaFs`` on the Origin Private File System through its own API: directory
 *  handles for the tree, synchronous access handles for the bytes (worker
 *  only).
 *
 *  A sync access handle is an exclusive lock, and opening one costs a round
 *  trip; materialising a case reads and writes dozens of files in a burst. So
 *  handles are kept open (up to ``maxOpen``) while a burst lasts and all
 *  closed after ``idleMs`` without an operation -- another tab's engine can
 *  then open them. Opening a handle another context holds is retried with
 *  back-off before it fails (and the case falls back to the server). With
 *  ``idleMs: 0`` every operation opens and closes its own handle. */
export function opfsNativeFeaFs(
    root: OpfsDirHandleLike,
    opts: {idleMs?: number; maxOpen?: number; retries?: number; sleep?: (ms: number) => Promise<void>} = {},
): FeaFs & {closeAll(): Promise<void>} {
    const idleMs = opts.idleMs ?? 1500;
    const maxOpen = opts.maxOpen ?? 32;
    const retries = opts.retries ?? 5;
    const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
    const parts = (path: string) => path.split("/").filter(Boolean);
    const dirs = new Map<string, Promise<OpfsDirHandleLike>>();
    const dirOf = (segs: string[], create: boolean): Promise<OpfsDirHandleLike> => {
        const key = segs.join("/");
        const cached = dirs.get(key);
        if (cached) return cached;
        const p = (async () => {
            let d = root;
            for (const s of segs) d = await d.getDirectoryHandle(s, {create});
            return d;
        })();
        if (create) {
            dirs.set(key, p);
            p.catch(() => dirs.delete(key));
        }
        return p;
    };
    const fileOf = async (path: string, create: boolean): Promise<OpfsFileHandleLike | null> => {
        const segs = parts(path);
        const name = segs.pop()!;
        try {
            const d = await dirOf(segs, create);
            return await d.getFileHandle(name, {create});
        } catch {
            return null;
        }
    };
    const handles = new Map<string, Promise<OpfsSyncHandle>>();
    let idleTimer: ReturnType<typeof setTimeout> | null = null;

    const openHandle = async (fh: OpfsFileHandleLike): Promise<OpfsSyncHandle> => {
        for (let attempt = 0; ; attempt++) {
            try {
                return await fh.createSyncAccessHandle();
            } catch (err) {
                // Held by another context (another tab's engine): wait for its burst to end.
                if (attempt >= retries || (err as {name?: string})?.name !== "NoModificationAllowedError") throw err;
                await sleep(50 * 2 ** attempt);
            }
        }
    };
    // Operations in flight per handle: a handle in use is never closed under
    // them by the idle timer or the open-handle bound (concurrent cases
    // interleave at every await).
    const users = new Map<string, number>();
    const closeOne = async (path: string, force = false): Promise<void> => {
        const p = handles.get(path);
        if (!p || (!force && (users.get(path) ?? 0) > 0)) return;
        handles.delete(path);
        try {
            (await p).close();
        } catch {
            /* failed to open, or already closed */
        }
    };
    const closeAll = async (): Promise<void> => {
        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = null;
        await Promise.all([...handles.keys()].map((k) => closeOne(k)));
    };
    const withHandle = async <T>(path: string, create: boolean, fn: (h: OpfsSyncHandle) => T): Promise<T | null> => {
        const key = parts(path).join("/");
        let p = handles.get(key);
        if (p) {
            handles.delete(key); // re-insert: most recently used last
            handles.set(key, p);
        } else {
            const fh = await fileOf(path, create);
            if (!fh) return null;
            p = handles.get(key); // another operation may have opened it meanwhile
            if (!p) {
                p = openHandle(fh);
                handles.set(key, p);
                p.catch(() => handles.delete(key));
            }
        }
        users.set(key, (users.get(key) ?? 0) + 1);
        try {
            if (handles.size > maxOpen) {
                for (const k of [...handles.keys()]) {
                    if (handles.size <= maxOpen) break;
                    if (k !== key) await closeOne(k);
                }
            }
            const h = await p;
            return fn(h);
        } finally {
            const n = (users.get(key) ?? 1) - 1;
            if (n > 0) users.set(key, n);
            else users.delete(key);
            if (idleMs <= 0) await closeOne(key);
            else {
                if (idleTimer) clearTimeout(idleTimer);
                idleTimer = setTimeout(() => void closeAll(), idleMs);
                (idleTimer as {unref?: () => void}).unref?.();
            }
        }
    };
    const writeAll = (h: OpfsSyncHandle, bytes: Uint8Array, at: number) => {
        let done = 0;
        while (done < bytes.byteLength) {
            const n = h.write(bytes.subarray(done), {at: at + done});
            if (n <= 0) throw new Error("OPFS write wrote nothing");
            done += n;
        }
    };
    return {
        async mkdirTree(path) {
            await dirOf(parts(path), true);
        },
        async size(path) {
            const n = await withHandle(path, false, (h) => h.getSize());
            return n ?? -1;
        },
        async writeAt(path, offset, bytes) {
            const ok = await withHandle(path, true, (h) => {
                writeAll(h, bytes, offset);
                h.flush();
                return true;
            });
            if (!ok) throw new Error(`OPFS: cannot write ${path}`);
        },
        async writeFile(path, bytes) {
            const ok = await withHandle(path, true, (h) => {
                h.truncate(0);
                writeAll(h, bytes, 0);
                h.flush();
                return true;
            });
            if (!ok) throw new Error(`OPFS: cannot write ${path}`);
        },
        async read(path, start, length) {
            const out = await withHandle(path, false, (h) => {
                const buf = new Uint8Array(length);
                let done = 0;
                while (done < length) {
                    const n = h.read(buf.subarray(done), {at: start + done});
                    if (n <= 0) break;
                    done += n;
                }
                return done === length ? buf : buf.subarray(0, done);
            });
            if (!out) throw new Error(`OPFS: no file ${path}`);
            return out;
        },
        closeAll,
        async remove(path) {
            const segs = parts(path);
            const name = segs.pop();
            if (!name) return;
            const p = parts(path).join("/");
            const under = (k: string) => k === p || k.startsWith(`${p}/`);
            // An open handle locks its file: close every one under the path first.
            await Promise.all([...handles.keys()].filter(under).map((k) => closeOne(k, true)));
            for (const k of [...dirs.keys()]) if (under(k)) dirs.delete(k);
            try {
                const d = await dirOf(segs, false);
                await d.removeEntry(name, {recursive: true});
            } catch {
                /* missing is not an error */
            }
        },
    };
}

/** An in-memory ``FeaFs`` (tests). */
export function memoryFeaFs(): FeaFs & {files: Map<string, Uint8Array>} {
    const files = new Map<string, Uint8Array>();
    return {
        files,
        mkdirTree: async () => {},
        size: async (p) => files.get(p)?.byteLength ?? -1,
        async writeAt(p, offset, bytes) {
            const cur = files.get(p) ?? new Uint8Array(0);
            const next = new Uint8Array(Math.max(cur.byteLength, offset + bytes.byteLength));
            next.set(cur, 0);
            next.set(bytes, offset);
            files.set(p, next);
        },
        writeFile: async (p, bytes) => void files.set(p, bytes.slice()),
        read: async (p, start, length) => (files.get(p) ?? new Uint8Array(0)).slice(start, start + length),
        async remove(p) {
            for (const k of [...files.keys()]) if (k === p || k.startsWith(`${p}/`)) files.delete(k);
        },
    };
}

/** An in-memory ``FeaIndex``. */
export function memoryFeaIndex(): FeaIndex & {rows: Map<string, FeaStoreRecord>} {
    const rows = new Map<string, FeaStoreRecord>();
    return {
        rows,
        get: async (id) => (rows.has(id) ? {...rows.get(id)!} : undefined),
        put: async (rec) => void rows.set(rec.id, {...rec}),
        delete: async (id) => void rows.delete(id),
        all: async () => [...rows.values()].map((r) => ({...r})),
    };
}

/** The IndexedDB index (``FeaStoreDB``), via Dexie. A static import: the
 *  engine worker is bundled as one classic script, which cannot code-split. */
export async function dexieFeaIndex(): Promise<FeaIndex> {
    const db = new Dexie(FEA_STORE_DB);
    // ``pinned`` is a boolean, which IndexedDB cannot index: kept on the row only.
    db.version(1).stores({entries: "&id, sourceId, kind, lastAccess"});
    const table = db.table<FeaStoreRecord, string>("entries");
    await db.open();
    return {
        get: (id) => table.get(id),
        put: async (rec) => {
            await table.put({...rec, pinned: Boolean(rec.pinned)});
        },
        delete: (id) => table.delete(id),
        all: () => table.toArray(),
    };
}
