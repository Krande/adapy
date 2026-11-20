// The clash check and detail job flows, with their I/O injected (`../clashCheckStore` passes the
// real ones).

import { describeClashTarget, type ClashCheckOptions, type ClashCheckTarget, type ClashCheckResponse, type ClashDetailResponse } from "@/services/api/clashCheck";
import { ClashResultError, parseClashResult, type ClashResult } from "./result";
import { emptyDetailStats, parseDetailStats, type DetailStats } from "./detail";

// ---------------------------------------------------------------------------------------------
// Job orchestration. Pure aside from the injected `api`/`trackJob`/`wait`/`now`, so it runs under
// `node --test` against fakes -- no network, mirrors `assets/delivery.ts`'s `loadNode` split.
// ---------------------------------------------------------------------------------------------

export interface ClashFlowApi {
  runClashCheck(scope: string, body: { target: ClashCheckTarget; options?: ClashCheckOptions }): Promise<ClashCheckResponse>;
  runClashDetail(
    scope: string,
    body: { result_key: string; joint_ids: readonly string[]; spec: string; options?: Record<string, unknown> },
  ): Promise<ClashDetailResponse>;
  getClashResult(scope: string, key: string): Promise<unknown>;
  getDetailStats(scope: string, key: string): Promise<unknown>;
  jobStatus(jobId: string): Promise<{ status: string; error: string | null; stage?: string | null; progress?: number | null }>;
}

export interface ClashFlowDeps {
  api: ClashFlowApi;
  trackJob?: (opts: { jobId: string; label: string; derivedKey?: string; scope?: string }) => void;
  /** The running job's stage as it is polled (e.g. a detail job's `"detail 120/392"`). */
  onJobStage?: (stage: string | null) => void;
  /** Poll backoff. Defaults to a real 1.5s timer; tests inject an instant no-op. */
  wait?: (ms: number) => Promise<void>;
  now?: () => number;
}

const POLL_INTERVAL_MS = 1500;
/** Same 10-minute ceiling `assets/delivery.ts` uses, for the same reason: a capability no live
 *  pool advertises is an ordinary deployment state, not a bug, and an unbounded poll would render
 *  that as a spinner that never resolves. Counted from the job's last SIGN OF LIFE (a change of
 *  stage or progress), not from its start: a detail run of hundreds of joints moves for longer than
 *  ten minutes, and giving up on a job that is visibly getting on is not a timeout. */
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

async function pollToTerminal(deps: ClashFlowDeps, jobId: string, label: string): Promise<void> {
  const wait = deps.wait ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const now = deps.now ?? (() => Date.now());
  let startedAt = now();
  let lastSign: string | null = null;
  for (;;) {
    const status = await deps.api.jobStatus(jobId);
    const sign = `${status.stage ?? ""}|${status.progress ?? ""}`;
    if (lastSign !== null && sign !== lastSign) startedAt = now();
    lastSign = sign;
    deps.onJobStage?.(status.status === "running" ? (status.stage ?? null) : null);
    if (status.status === "done") return;
    if (status.status === "error" || status.status === "cancelled") {
      throw new ClashResultError(`${label} ${status.status}${status.error ? `: ${status.error}` : ""}`);
    }
    if (now() - startedAt >= POLL_TIMEOUT_MS) {
      throw new ClashResultError(
        `${label} is still ${status.status} after ${Math.round(POLL_TIMEOUT_MS / 60000)} min without progress. The job was not ` +
          `cancelled -- running the check again will pick up its result if it does finish.`,
      );
    }
    await wait(POLL_INTERVAL_MS);
  }
}

/** Run a clash check (or read a cached one) and return the parsed result. `cached: true` on the
 *  response means the result already sits at `derived_key` -- NOTHING is enqueued, which is
 *  what lets a repeat run be a cache hit rather than a job (pinned by a test). */
export async function runClashCheckFlow(
  deps: ClashFlowDeps,
  scope: string,
  target: ClashCheckTarget,
  options: ClashCheckOptions,
): Promise<{ result: ClashResult; derivedKey: string; cached: boolean }> {
  const resp = await deps.api.runClashCheck(scope, { target, options });
  if (!resp.cached) {
    if (!resp.job_id) {
      throw new ClashResultError("clash-check reported neither a cached result nor a job id");
    }
    deps.trackJob?.({
      jobId: resp.job_id,
      label: `Clash check: ${describeClashTarget(target)}`,
      derivedKey: resp.derived_key,
    });
    await pollToTerminal(deps, resp.job_id, "clash check");
  }
  const doc = await deps.api.getClashResult(scope, resp.derived_key);
  const result = parseClashResult(doc);
  return { result, derivedKey: resp.derived_key, cached: resp.cached };
}

/** Hand a group's joints off to one of its `applicable` specs. Returns the derived key of the
 *  overlay GLB (+ `.stats.json`) the caller should load with `overlay_file_in_scene` -- loading
 *  into the scene is deliberately NOT this module's job, the same split `assets/delivery.ts`
 *  keeps between fetching/validating a build and `SceneHandle.loadModelFromUrl` actually loading
 *  it. */
export async function runClashDetailFlow(
  deps: ClashFlowDeps,
  scope: string,
  resultKey: string,
  jointIds: readonly string[],
  spec: string,
  options?: Record<string, unknown>,
): Promise<{ derivedKey: string; glbKey: string | null; cached: boolean; stats: DetailStats }> {
  const resp = await deps.api.runClashDetail(scope, { result_key: resultKey, joint_ids: jointIds, spec, options });
  const cached = resp.cached ?? false;
  if (!cached) {
    if (!resp.job_id) {
      throw new ClashResultError("clash-detail reported neither a cached result nor a job id");
    }
    deps.trackJob?.({ jobId: resp.job_id, label: `Detail: ${spec}`, derivedKey: resp.derived_key });
    await pollToTerminal(deps, resp.job_id, `detail (${spec})`);
  }
  // The take-off is part of what a detail run PRODUCED, so it is read here rather than by the
  // panel: a surface that fetched it itself could show a table for a run whose document had not
  // been written yet. A document that cannot be read is not a failed run -- the geometry is
  // built and loadable -- so this degrades to an empty take-off rather than throwing.
  let stats: DetailStats = emptyDetailStats();
  try {
    stats = parseDetailStats(await deps.api.getDetailStats(scope, resp.derived_key));
  } catch {
    stats = emptyDetailStats();
  }
  return { derivedKey: resp.derived_key, glbKey: resp.glb_key ?? null, cached, stats };
}
