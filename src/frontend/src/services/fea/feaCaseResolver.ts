// Load combinations the bake did not store, materialised on request.
//
// A bake_version 4 manifest lists its stored cases as field steps and the
// combinations it left out under ``combination_steps``: recipes over stored
// cases. Showing one means MATERIALISING it -- superposing the stored strides
// (or, for a complex case at a non-zero phase, re-reading the raw records) --
// which happens somewhere else than the paint path: today on the server
// (``GET .../fea/case``, 200 overlay or 202 + poll), later in the browser too.
//
// This module is the one place that knows where. ``resolveCase`` returns the
// case's overlay -- single-step blobs under ``cases/<n>-<hash8>/`` and the
// case's own ranges -- through a ``CaseEngine``:
//
//   * ``serverCaseEngine``: the REST route.
//   * the "local" engine (feaLocalEngine.ts): superpose the baked strides in
//     a worker (adacpp_fea wasm kernel + OPFS store) and hand back an overlay
//     of the same shape, its blobs served by an OPFS fetcher route.
//     ``chooseCaseEngine`` picks it for a recipe the browser may superpose;
//     it then decides per case (policy setting, capability probe, quota,
//     device memory -- feaEngineChoice.ts) and falls back to the server on
//     any failure. Everything above this module consumes ``CaseResolution``
//     and does not care which engine produced it (``engine`` says).
//
// Resolutions are cached per (source, case, recipe hash) for the page's
// life, and concurrent requests for the same case share one request. Every
// case has a status the step picker shows (``on-request`` -> ``computing`` ->
// ``ready``, or ``error``), published to subscribers as it changes.

import type {
    FeaCaseOverlay,
    FeaCombinationStep,
    FeaEnvelope,
    FeaManifest,
    FeaManifestField,
    ScopeUrl,
} from "../viewerApi";
import type {Fetcher, PollDeps, StatusFn} from "../feaManifestPoll";
import type {LocalEngine} from "./feaLocalEngine";
import {caseFieldView, caseRelativePrefix, isCaseRef, type FeaStepRef} from "./feaStepRef";

export type CaseStatus = "on-request" | "computing" | "ready" | "error";

export interface CaseStatusInfo {
    status: CaseStatus;
    /** While computing: the job's stage and 0..1 progress. */
    stage?: string;
    progress?: number;
    /** On error: the message, and the HTTP status when there was one (409 =
     *  the base bake is missing or stale -- re-fetch the manifest). */
    error?: string;
    httpStatus?: number;
    /** On ready: whether a job had to run (false = it was already cached). */
    computed?: boolean;
}

export interface CaseResolution {
    overlay: FeaCaseOverlay;
    /** The case's blob directory relative to the bake directory, for a
     *  fetcher rooted at ``_derived/<src>.fea/`` (see ``caseFieldView``). */
    relPrefix: string;
    engine: CaseEngine["kind"];
    /** True when a job ran to produce it; false when it was cached. */
    computed: boolean;
}

export interface CaseRequest {
    scope: ScopeUrl;
    sourceKey: string;
    manifest: FeaManifest;
    step: FeaCombinationStep;
    /** Forwarded to the route; the server materialises every field anyway. */
    field?: string;
}

/** Where a case is materialised. P2 adds ``kind: "local"``. */
export interface CaseEngine {
    kind: "server" | "local";
    resolve(
        req: CaseRequest,
        onStatus: (info: CaseStatusInfo) => void,
    ): Promise<{overlay: FeaCaseOverlay; computed: boolean; engine?: "server" | "local"}>;
}

export interface ServerEngineDeps {
    fetcher: Fetcher;
    convertStatus: StatusFn;
    apiBase: string;
    pollMs?: number;
    timeoutMs?: number;
    sleep?: (ms: number) => Promise<void>;
    now?: () => number;
}

async function defaultServerDeps(): Promise<ServerEngineDeps> {
    // Lazy: the API client touches sessionStorage at import, which the pure
    // parts of this module (and Node tests of them) must not pay for.
    const [{authedFetch}, {conversionApi}, {runtime}] = await Promise.all([
        import("../api/client"),
        import("../api/conversion"),
        import("@/runtime/config"),
    ]);
    return {
        fetcher: authedFetch as unknown as Fetcher,
        convertStatus: (jobId) => conversionApi.convertStatus(jobId),
        apiBase: runtime.apiBase(),
    };
}

/** The REST engine: ``GET .../fea/case`` -- 200 is the overlay, 202 queues a
 *  job that is polled exactly like the manifest's, then the route is asked
 *  again. */
export function serverCaseEngine(deps?: ServerEngineDeps): CaseEngine {
    return {
        kind: "server",
        async resolve(req, onStatus) {
            const d = deps ?? (await defaultServerDeps());
            const {fetchFeaCase} = await import("../feaManifestPoll");
            let computed = false;
            const pollDeps: PollDeps & {caseN: number; field?: string} = {
                fetcher: d.fetcher,
                convertStatus: d.convertStatus,
                apiBase: d.apiBase,
                scope: req.scope,
                sourceKey: req.sourceKey,
                caseN: req.step.n,
                field: req.field,
                pollMs: d.pollMs,
                timeoutMs: d.timeoutMs,
                sleep: d.sleep,
                now: d.now,
                onProgress: ({stage, progress}) => {
                    computed = true;
                    onStatus({status: "computing", stage, progress});
                },
            };
            const overlay = await fetchFeaCase(pollDeps);
            return {overlay, computed};
        },
    };
}

let _serverEngine: CaseEngine | null = null;
let _localEngine: Promise<LocalEngine | null> | null = null;

function serverEngine(): CaseEngine {
    if (!_serverEngine) _serverEngine = serverCaseEngine();
    return _serverEngine;
}

/** The browser engine (services/fea/feaLocalEngine.ts) with its real wiring,
 *  created on first use; null where it cannot exist (no Worker: node, SSR). */
function localEngine(): Promise<LocalEngine | null> {
    if (!_localEngine) {
        _localEngine = (async () => {
            if (typeof Worker === "undefined") return null;
            try {
                const {defaultLocalEngineDeps, makeLocalCaseEngine} = await import("./feaLocalEngine");
                return makeLocalCaseEngine(await defaultLocalEngineDeps(serverEngine()));
            } catch (err) {
                console.warn("[fea] browser case engine unavailable", err);
                return null;
            }
        })();
    }
    return _localEngine;
}

/** Use ``engine`` as the browser engine (tests; null = none; undefined =
 *  back to the default wiring). */
export function setLocalCaseEngine(engine: LocalEngine | null | undefined): void {
    _localEngine = engine === undefined ? null : Promise.resolve(engine);
}

/** A case the manifest says the browser may superpose (``client_tier_a``, no
 *  ``needs_raw``) goes to the local engine, which decides per case (policy,
 *  capability probe, quota, device memory -- feaEngineChoice.ts) and falls back
 *  to the server on any failure; every other case goes to the server. */
const autoEngine: CaseEngine = {
    kind: "local",
    async resolve(req, onStatus) {
        const local = await localEngine();
        if (!local) return {...(await serverEngine().resolve(req, onStatus)), engine: "server"};
        return local.resolve(req, onStatus);
    },
};

/** The engine a case is materialised with: the server, or (for a recipe the
 *  browser may superpose, where a Worker exists) the local engine, which
 *  itself falls back to the server. */
export function chooseCaseEngine(manifest: FeaManifest, step: FeaCombinationStep): CaseEngine {
    if (!manifest.lazy_cases?.client_tier_a || step.needs_raw) return serverEngine();
    if (typeof Worker === "undefined" && !_localEngine) return serverEngine();
    return autoEngine;
}

// ── cache + status ─────────────────────────────────────────────────────────

const RESOLVED = new Map<string, CaseResolution>();
const INFLIGHT = new Map<string, Promise<CaseResolution>>();
const STATUS = new Map<string, CaseStatusInfo>();
const LISTENERS = new Set<() => void>();
let statusVersion = 0;

/** Cache key of one case of one source: the recipe hash is part of it, so a
 *  re-solved deck whose combination changed never hits an old overlay. */
export function caseCacheKey(sourceKey: string, n: number, recipeHash: string): string {
    return `${sourceKey.replace(/^\/+/, "")}#${n}#${recipeHash}`;
}

function setStatus(key: string, info: CaseStatusInfo): void {
    STATUS.set(key, info);
    statusVersion++;
    for (const l of [...LISTENERS]) {
        try {
            l();
        } catch (err) {
            console.warn("[fea] case status listener failed", err);
        }
    }
}

/** The status of one case; ``on-request`` until something asks for it. */
export function caseStatus(sourceKey: string, step: Pick<FeaCombinationStep, "n" | "recipe_hash">): CaseStatusInfo {
    return STATUS.get(caseCacheKey(sourceKey, step.n, step.recipe_hash)) ?? {status: "on-request"};
}

/** Subscribe to status changes (any case). For ``useSyncExternalStore``. */
export function subscribeCaseStatus(listener: () => void): () => void {
    LISTENERS.add(listener);
    return () => {
        LISTENERS.delete(listener);
    };
}

/** Changes whenever any status does -- a ``useSyncExternalStore`` snapshot. */
export function caseStatusVersion(): number {
    return statusVersion;
}

/** Forget every resolved case (scene replaced, manifest re-fetched). */
export function clearCaseCache(): void {
    RESOLVED.clear();
    INFLIGHT.clear();
    STATUS.clear();
    ENVELOPES.clear();
    statusVersion++;
    for (const l of [...LISTENERS]) l();
}

export function findCombinationStep(
    manifest: Pick<FeaManifest, "combination_steps"> | null | undefined,
    caseN: number,
): FeaCombinationStep | null {
    return manifest?.combination_steps?.find((c) => c.n === caseN) ?? null;
}

/** A case already resolved, without asking anyone. */
export function peekCase(
    manifest: FeaManifest | null | undefined,
    sourceKey: string,
    caseN: number,
): CaseResolution | null {
    const step = findCombinationStep(manifest, caseN);
    if (!step) return null;
    return RESOLVED.get(caseCacheKey(sourceKey, step.n, step.recipe_hash)) ?? null;
}

export interface ResolveCaseOptions {
    field?: string;
    /** Aborts this caller's wait; a request other callers share keeps going. */
    signal?: AbortSignal;
    /** Override the engine (tests; P2's policy otherwise picks). */
    engine?: CaseEngine;
    onStatus?: (info: CaseStatusInfo) => void;
}

/** Materialise (or fetch the cached materialisation of) combination ``caseN``. */
export async function resolveCase(
    manifest: FeaManifest,
    source: {scope: ScopeUrl; sourceKey: string},
    caseN: number,
    opts: ResolveCaseOptions = {},
): Promise<CaseResolution> {
    const step = findCombinationStep(manifest, caseN);
    if (!step) {
        throw new Error(`case ${caseN} is not a combination this bake left to compute`);
    }
    const key = caseCacheKey(source.sourceKey, step.n, step.recipe_hash);
    const done = RESOLVED.get(key);
    if (done) return done;

    let shared = INFLIGHT.get(key);
    if (!shared) {
        const engine = opts.engine ?? chooseCaseEngine(manifest, step);
        setStatus(key, {status: "computing", stage: "requesting"});
        shared = (async () => {
            try {
                const {overlay, computed, engine: produced} = await engine.resolve(
                    {scope: source.scope, sourceKey: source.sourceKey, manifest, step, field: opts.field},
                    (info) => {
                        setStatus(key, info);
                        opts.onStatus?.(info);
                    },
                );
                if (overlay?.case?.n !== undefined && overlay.case.n !== step.n) {
                    throw new Error(`case overlay is for case ${overlay.case.n}, asked for ${step.n}`);
                }
                const resolution: CaseResolution = {
                    overlay,
                    relPrefix: caseRelativePrefix(overlay, source.sourceKey, manifest),
                    engine: produced ?? engine.kind,
                    computed,
                };
                RESOLVED.set(key, resolution);
                setStatus(key, {status: "ready", computed});
                return resolution;
            } catch (err) {
                const aborted = err instanceof DOMException && err.name === "AbortError";
                if (aborted) {
                    STATUS.delete(key);
                    statusVersion++;
                    for (const l of [...LISTENERS]) l();
                } else {
                    const httpStatus = (err as {status?: unknown})?.status;
                    setStatus(key, {
                        status: "error",
                        error: err instanceof Error ? err.message : String(err),
                        ...(typeof httpStatus === "number" ? {httpStatus} : {}),
                    });
                }
                throw err;
            } finally {
                INFLIGHT.delete(key);
            }
        })();
        INFLIGHT.set(key, shared);
    }
    if (!opts.signal) return shared;
    const signal = opts.signal;
    if (signal.aborted) throw new DOMException("aborted", "AbortError");
    return new Promise<CaseResolution>((resolve, reject) => {
        const onAbort = () => reject(new DOMException("aborted", "AbortError"));
        signal.addEventListener("abort", onAbort, {once: true});
        shared!.then(
            (r) => {
                signal.removeEventListener("abort", onAbort);
                resolve(r);
            },
            (e) => {
                signal.removeEventListener("abort", onAbort);
                reject(e);
            },
        );
    });
}

// ── envelope ───────────────────────────────────────────────────────────────

const ENVELOPES = new Map<string, Promise<FeaEnvelope | null>>();

/** One field's range over every load combination, or null when the server
 *  does not offer it (older server, 404/501, any failure). Cached per
 *  (source, field) for the page's life; failures are cached too, so a server
 *  without the route is asked once. */
export function resolveEnvelope(
    manifest: FeaManifest,
    source: {scope: ScopeUrl; sourceKey: string},
    fieldName: string,
    deps?: ServerEngineDeps,
): Promise<FeaEnvelope | null> {
    if (!manifest.combination_steps?.length) return Promise.resolve(null);
    const key = `${source.sourceKey.replace(/^\/+/, "")}#${fieldName}#${manifest.combination_steps.length}`;
    let p = ENVELOPES.get(key);
    if (!p) {
        p = (async () => {
            // The browser engine first, when the bake allows it and no server
            // deps were injected (tests); it answers null to leave it to the server.
            if (!deps && manifest.lazy_cases?.client_tier_a) {
                const local = await localEngine();
                const env = await local
                    ?.envelope({scope: source.scope, sourceKey: source.sourceKey, manifest, field: fieldName})
                    .catch(() => null);
                if (env) return env;
            }
            try {
                const d = deps ?? (await defaultServerDeps());
                const {fetchFeaEnvelope} = await import("../feaManifestPoll");
                const env = await fetchFeaEnvelope({
                    fetcher: d.fetcher,
                    convertStatus: d.convertStatus,
                    apiBase: d.apiBase,
                    scope: source.scope,
                    sourceKey: source.sourceKey,
                    field: fieldName,
                    pollMs: d.pollMs,
                    timeoutMs: d.timeoutMs,
                    sleep: d.sleep,
                    now: d.now,
                });
                return env && env.scalar_range ? env : null;
            } catch {
                return null;
            }
        })();
        ENVELOPES.set(key, p);
    }
    return p;
}

/** The field and blob step index to READ for ``ref``: the field itself at its
 *  stored index, or (a combination) the case view of it at step 0, resolving
 *  the case first if needed. Null when the case does not carry the field. */
export async function fieldAtStep(
    manifest: FeaManifest,
    field: FeaManifestField,
    ref: FeaStepRef,
    source: {scope: ScopeUrl; sourceKey: string},
    opts: Omit<ResolveCaseOptions, "field"> = {},
): Promise<{field: FeaManifestField; step: number} | null> {
    if (!isCaseRef(ref)) return {field, step: ref.stored};
    const resolution = peekCase(manifest, source.sourceKey, ref.case)
        ?? (await resolveCase(manifest, source, ref.case, {...opts, field: field.name_canonical}));
    const view = caseFieldView(field, resolution.overlay, resolution.relPrefix);
    return view ? {field: view, step: 0} : null;
}

/** Start resolving a case without waiting (the next frame of an animation).
 *  Failures are recorded as the case's status, not thrown. */
export function prefetchCase(
    manifest: FeaManifest,
    source: {scope: ScopeUrl; sourceKey: string},
    caseN: number,
    opts: Omit<ResolveCaseOptions, "signal"> = {},
): void {
    void resolveCase(manifest, source, caseN, opts).catch(() => {});
}
