// Core jobs the browser runs: the clash check and its detailing, component builds, the selection
// export, equipment bounding boxes and scene utilities -- work that used to be a server worker
// job, run in this browser's Pyodide worker first (ada.comms.rest.wasm_jobs), the worker after it.
//
// THE ORDER is the execution policy's "core_job" operation, one row per family (the qualifier):
// an administrator can keep clash detailing on the worker and the check in the browser, globally
// or per user, and the user may move an allowed engine to the front (Settings > Execution engine).
//
// THE ROUTE STAYS THE ONE THAT COMPOSES THE JOB. A request the browser runs first carries
// `X-Ada-Run-On: browser`; the route composes the job exactly as for a worker (derived key,
// options, audit row) and answers with the job as a CLAIM (`browser_job`) instead of a job id --
// no worker job was queued. `settleCoreJob` then takes the claim, runs it in Pyodide, stores each
// output through the API (the server decides where each goes and checks it), and completes it.
// What comes back is the route's answer with `job_id` set to the claim's id, now done, so every
// caller polls it as it polled a worker job and finds it finished.
//
// FALLBACK. When the browser cannot run it (no Pyodide, a checker or spec no baked wheel
// registers, a crash) and the policy lists the worker after it, the SAME job record is handed to
// its pool (`to-worker`, judged as an explicit worker choice) and the caller polls the worker job
// under the same id. A failure the job itself reports with an explanation (a builder that
// declines the members, a selection the source does not contain) is final: the worker would say
// the same.

import {runtime} from "@/runtime/config";
import {authedFetch, jsonOrThrow, RUN_ON_HEADER, type ScopeUrl} from "@/services/api/client";

import {NoAllowedEngineError, planExecution, type ExecutionPlan, type ResolveOptions} from "./executionPolicy";
import type {CoreJobFamily} from "./executionPolicyModel";

export {CORE_JOB_FAMILIES, type CoreJobFamily} from "./executionPolicyModel";

/** A claimed job, as the route returns it (`browser_jobs.claim_document`). */
export interface BrowserJobClaim {
    id: string;
    kind: string;
    family: CoreJobFamily | null;
    source_key: string;
    derived_key: string;
    conversion_options: Record<string, unknown>;
    target_capability: string | null;
    scope: string;
}

/** What a run in Pyodide answers (`wasm_jobs.run`). */
export interface CoreJobRun {
    status: "done" | "error";
    error: string | null;
    traceback?: string | null;
    outputs: Record<string, Uint8Array>;
}

export interface CoreJobDeps {
    /** Whether this page can run Pyodide at all (a Worker). */
    pyodideAvailable(): boolean;
    start(scope: ScopeUrl, id: string): Promise<BrowserJobClaim>;
    run(claim: BrowserJobClaim, onProgress?: (stage: string, fraction: number) => void): Promise<CoreJobRun>;
    putOutput(scope: ScopeUrl, id: string, name: string, bytes: Uint8Array): Promise<void>;
    complete(scope: ScopeUrl, id: string, body: {status: "done" | "error"; error?: string | null; traceback?: string | null}): Promise<void>;
    toWorker(scope: ScopeUrl, id: string, reason: string): Promise<{job_id: string}>;
    log?(msg: string): void;
    onProgress?(stage: string, fraction: number): void;
}

const browserJobsBase = (scope: ScopeUrl, id: string) =>
    `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/browser-jobs/${encodeURIComponent(id)}`;

/** The live deps. The Pyodide worker is imported on first use, never by a page that runs no job. */
export const realCoreJobDeps: CoreJobDeps = {
    pyodideAvailable: () => typeof Worker !== "undefined",
    async start(scope, id) {
        const r = await authedFetch(`${browserJobsBase(scope, id)}/start`, {method: "POST"});
        return jsonOrThrow<BrowserJobClaim>(r, `browser job ${id}: start`);
    },
    async run(claim, onProgress) {
        const [{runCoreJobViaPyodide}, {getAccessToken}] = await Promise.all([
            import("@/utils/pyodide/pyodide_converter"),
            import("@/services/auth/oidc"),
        ]);
        const token = getAccessToken();
        return runCoreJobViaPyodide(claim, {
            apiBase: runtime.apiBase(),
            authHeaders: token ? {Authorization: `Bearer ${token}`} : undefined,
            onProgress,
        });
    },
    async putOutput(scope, id, name, bytes) {
        const r = await authedFetch(`${browserJobsBase(scope, id)}/outputs/${encodeURIComponent(name)}`, {
            method: "PUT",
            headers: {"Content-Type": "application/octet-stream"},
            body: bytes as BodyInit,
        });
        await jsonOrThrow<unknown>(r, `browser job ${id}: output ${name}`);
    },
    async complete(scope, id, body) {
        const r = await authedFetch(`${browserJobsBase(scope, id)}/complete`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
        });
        await jsonOrThrow<unknown>(r, `browser job ${id}: complete`);
    },
    async toWorker(scope, id, reason) {
        const r = await authedFetch(`${browserJobsBase(scope, id)}/to-worker`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({reason}),
        });
        return jsonOrThrow<{job_id: string}>(r, `browser job ${id}: to worker`);
    },
    log: (msg) => console.info(msg),
};

/** The order to run a family in: the policy's "core_job" row for it; Pyodide only where it can run. */
export function planCoreJob(
    family: CoreJobFamily,
    opts: Omit<ResolveOptions, "supports"> & {pyodideAvailable?: boolean} = {},
): ExecutionPlan {
    const pyodide = opts.pyodideAvailable ?? realCoreJobDeps.pyodideAvailable();
    return planExecution("core_job", family, (e) => e === "pyodide" && pyodide, opts);
}

/** The headers of a request whose job `plan` runs: the browser first (`X-Ada-Run-On: browser`, the
 * route answers with a claim), or the worker chosen over it (`worker`, judged by the server). */
export function coreJobHeaders(plan: ExecutionPlan): Record<string, string> {
    const first = plan.engines[0];
    if (!first) throw new NoAllowedEngineError(plan);
    if (first === "pyodide") return {[RUN_ON_HEADER]: "browser"};
    return plan.browserPath ? {[RUN_ON_HEADER]: "worker"} : {};
}

/** A failure the job itself explains (it still produced its report): the worker would answer the same. */
function reportedFailure(run: CoreJobRun): boolean {
    return run.status === "error" && Object.keys(run.outputs).length > 0;
}

export class CoreJobError extends Error {
    constructor(message: string, readonly traceback?: string | null) {
        super(message);
        this.name = "CoreJobError";
    }
}

/** Run a claim in the browser and store what it produced. Throws `CoreJobError` when it failed. */
async function runClaim(scope: ScopeUrl, claim: BrowserJobClaim, deps: CoreJobDeps, plan: ExecutionPlan): Promise<void> {
    const workerNext = plan.engines.includes("worker");
    const label = `[core job] ${claim.kind} ${claim.id}`;
    let run: CoreJobRun;
    try {
        const taken = await deps.start(scope, claim.id);
        run = await deps.run(taken, deps.onProgress);
    } catch (err) {
        throw new CoreJobError(err instanceof Error ? err.message : String(err));
    }
    if (run.status === "error" && !reportedFailure(run) && workerNext) {
        // Not stored, not completed: the claim goes to the worker as it is.
        throw new CoreJobError(run.error ?? "the browser could not run the job", run.traceback);
    }
    for (const [name, bytes] of Object.entries(run.outputs)) {
        await deps.putOutput(scope, claim.id, name, bytes);
    }
    await deps.complete(scope, claim.id, {status: run.status, error: run.error, traceback: run.traceback ?? null});
    deps.log?.(`${label}: ${run.status} in the browser`);
    if (run.status === "error") throw new CoreJobFinal(run.error ?? "the job failed", run.traceback);
}

/** A failure that ended the claim (stored and completed): not handed to the worker. */
class CoreJobFinal extends CoreJobError {}

/** A route's answer with a claim in it settled: run in the browser (or handed to the worker), and
 * `job_id` set to the job to poll -- done when the browser ran it. An answer without a claim (a
 * worker job, a cache hit) is returned as it is. */
export async function settleCoreJob<R extends {job_id?: string | null; browser_job?: BrowserJobClaim | null}>(
    scope: ScopeUrl,
    resp: R,
    plan: ExecutionPlan,
    deps: CoreJobDeps = realCoreJobDeps,
): Promise<R & {job_id: string | null; ran_in_browser?: boolean}> {
    const claim = resp.browser_job;
    if (!claim) return resp as R & {job_id: string | null};
    // The claim names the scope its job was made in (a component build's is not the page's).
    scope = claim.scope || scope;
    const {browser_job: _claim, ...rest} = resp;
    try {
        await runClaim(scope, claim, deps, plan);
        return {...(rest as R), job_id: claim.id, ran_in_browser: true};
    } catch (err) {
        if (err instanceof CoreJobFinal || !plan.engines.includes("worker")) {
            if (!(err instanceof CoreJobFinal)) {
                // Nothing else may run it: end the claim with the browser's reason.
                await deps
                    .complete(scope, claim.id, {status: "error", error: (err as Error).message})
                    .catch(() => undefined);
            }
            throw err;
        }
        const reason = err instanceof Error ? err.message : String(err);
        deps.log?.(`[core job] ${claim.kind} ${claim.id}: the browser could not run it (${reason}); handing it to the worker`);
        const handed = await deps.toWorker(scope, claim.id, reason);
        return {...(rest as R), job_id: handed.job_id};
    }
}

/** POST a route that submits a core job of `family`, with the headers the policy asks for, and
 * settle the answer (`settleCoreJob`). */
export async function postCoreJob<R extends {job_id?: string | null; browser_job?: BrowserJobClaim | null}>(
    scope: ScopeUrl,
    url: string,
    body: unknown,
    family: CoreJobFamily,
    what: string,
    deps: CoreJobDeps = realCoreJobDeps,
): Promise<R & {job_id: string | null; ran_in_browser?: boolean}> {
    const plan = planCoreJob(family, {pyodideAvailable: deps.pyodideAvailable()});
    const r = await authedFetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json", ...coreJobHeaders(plan)},
        body: JSON.stringify(body),
    });
    return settleCoreJob(scope, await jsonOrThrow<R>(r, what), plan, deps);
}
