// Ask a provider for a collection, then publish what it staged.
//
// THE ASSETS TAB ONLY EVER SHOWED WHAT WAS ALREADY PUBLISHED. A provider whose
// collections live in a system of record -- a design database a worker can read
// and the cluster cannot -- had no way in from here: the request had to be made
// in that provider's own panel, and core's browser stayed empty until it was.
//
// TWO JOBS, AND CORE NAMES NEITHER.
//
//   1. The provider's own job, as its spec DECLARES it (`asset_collection_request`:
//      the options to send and the option that names the collection). It fetches
//      the collection and STAGES it -- the bytes land under `assets/_staging/<id>/`
//      -- and returns that id as `asset_staging_id`. That is the whole contract.
//   2. Core's publish (`POST /assets/publish`) of that staging id under the
//      provider's id: the provider derives, core writes, manifests last. The same
//      route an upload uses, so a requested collection and an uploaded one are
//      published by one path.
//
// SLOW BY NATURE, SO THE TOAST HOLDS IT. Fetching a whole hierarchy from a live
// system can take most of an hour. Each job goes to the global toast, and a
// request that finishes while nobody is watching is not lost: its staged bytes
// are in the store, and `GET /assets/staging` lists them for a publish by hand.
//
// Pure apart from the injected api, so the flow runs under plain node.

import type { AssetCollectionRequest, AssetNodeRequest } from "@/services/assetScopeCollections";

export class CollectionRequestError extends Error {}

export interface CollectionRequestApi {
  pluginJob(
    pluginId: string,
    body: { options: Record<string, unknown> },
    opts: { scope: string },
  ): Promise<{ job_id: string; derived_key: string }>;
  publish(
    scope: string,
    body: { provider: string; staging_id: string },
  ): Promise<{ job_id: string; derived_key: string }>;
  jobStatus(jobId: string): Promise<{ status: string; stage?: string; error: string | null }>;
  /** A job's JSON summary at `key` (gzip-at-rest, decompressed by the blob route). */
  readJson(scope: string, key: string): Promise<unknown>;
}

export interface CollectionRequestDeps {
  api: CollectionRequestApi;
  trackJob?: (opts: { jobId: string; label: string; derivedKey?: string }) => void;
  onStage?: (stage: string) => void;
  wait?: (ms: number) => Promise<void>;
  now?: () => number;
}

/** What a finished request published -- or, when `unchanged`, the existing publish it found still
 *  current (nothing was staged or published; `stagingId` is empty). */
export interface CollectionRequestOutcome {
  stagingId: string;
  collection: string;
  revision: string;
  subjects: readonly string[];
  unchanged?: boolean;
  /** The provider's own words for an unchanged answer, e.g. what it compared. */
  message?: string;
}

/** A provider's "nothing changed" answer: `{asset_unchanged: true, revision, subjects?, message?}`
 *  in place of `asset_staging_id`. Its source is exactly what it last published, so publishing it
 *  again would only add a revision that says the same thing. Null for any other summary. */
export function unchangedOf(summary: unknown): { revision: string; subjects: string[]; message: string | null } | null {
  if (!summary || typeof summary !== "object") return null;
  const s = summary as Record<string, unknown>;
  if (s.asset_unchanged !== true) return null;
  return {
    revision: typeof s.revision === "string" ? s.revision : "",
    subjects: Array.isArray(s.subjects) ? s.subjects.map(String) : [],
    message: typeof s.message === "string" ? s.message : null,
  };
}

const POLL_INTERVAL_MS = 3000;
/** The provider's fetch. Generous, because a whole hierarchy from a live design
 *  system is measured in tens of minutes, and giving up early would strand a
 *  result that is about to arrive. */
const FETCH_TIMEOUT_MS = 90 * 60_000;
const PUBLISH_TIMEOUT_MS = 15 * 60_000;

/** The options core sends: the declared ones, the collection under the declared
 *  name, and a `requested_at` stamp so a second request is a second job rather
 *  than a cache hit on the first. */
export function requestOptions(
  req: AssetCollectionRequest,
  collection: string,
  requestedAt: string,
): Record<string, unknown> {
  return { ...req.options, [req.collectionOption]: collection, requested_at: requestedAt };
}

/** The staging id a provider's summary hands back, or a sentence saying why not. (An
 *  `asset_unchanged` answer is handled before this is asked -- see `unchangedOf`.) */
export function stagingIdOf(summary: unknown): string {
  const id =
    summary && typeof summary === "object" ? (summary as Record<string, unknown>).asset_staging_id : undefined;
  if (typeof id !== "string" || !id.trim()) {
    throw new CollectionRequestError(
      "the provider's job finished without an asset_staging_id, so there is nothing to publish. " +
        "A provider that declares asset_collection_request must stage what it fetched and return its id.",
    );
  }
  return id.trim();
}

/** The options for a node request: the collection request's, plus the node's id -- as the
 *  browser shows it -- under the declared `nodeOption`, as a one-element list; and its label under
 *  `labelOption` when the provider declared one and a label is known. */
export function nodeRequestOptions(
  req: AssetNodeRequest,
  collection: string,
  node: string,
  requestedAt: string,
  label?: string,
): Record<string, unknown> {
  const options: Record<string, unknown> = { ...requestOptions(req, collection, requestedAt), [req.nodeOption]: [node] };
  if (req.labelOption && label) options[req.labelOption] = [label];
  return options;
}

export async function requestCollection(
  deps: CollectionRequestDeps,
  scope: string,
  providerId: string,
  req: AssetCollectionRequest,
  collection: string,
): Promise<CollectionRequestOutcome> {
  const now = deps.now ?? (() => Date.now());
  const options = requestOptions(req, collection, new Date(now()).toISOString());
  return stageAndPublish(deps, scope, providerId, req, options, collection, collection);
}

/** Ask a provider for one node of a published collection -- its geometry, typically, where the
 *  collection was published as a hierarchy alone -- and publish what it staged. The same two jobs
 *  as a collection request. */
export async function requestNode(
  deps: CollectionRequestDeps,
  scope: string,
  providerId: string,
  req: AssetNodeRequest,
  collection: string,
  node: string,
  nodeLabel?: string,
): Promise<CollectionRequestOutcome> {
  const now = deps.now ?? (() => Date.now());
  const options = nodeRequestOptions(req, collection, node, new Date(now()).toISOString(), nodeLabel);
  return stageAndPublish(deps, scope, providerId, req, options, collection, `${collection} / ${nodeLabel ?? node}`);
}

async function stageAndPublish(
  deps: CollectionRequestDeps,
  scope: string,
  providerId: string,
  req: AssetCollectionRequest,
  options: Record<string, unknown>,
  collection: string,
  what: string,
): Promise<CollectionRequestOutcome> {
  const stage = (s: string) => deps.onStage?.(s);

  stage(`asking ${req.pluginId} for ${what}`);
  const fetchJob = await deps.api.pluginJob(req.pluginId, { options }, { scope });
  deps.trackJob?.({ jobId: fetchJob.job_id, label: `${req.label}: ${what}`, derivedKey: fetchJob.derived_key });
  await pollToTerminal(deps, fetchJob.job_id, FETCH_TIMEOUT_MS, `the request for ${what}`, stage);
  const summary = await deps.api.readJson(scope, fetchJob.derived_key);
  // Nothing changed at the source since the provider last published it: no staging, no publish.
  const same = unchangedOf(summary);
  if (same) {
    return {
      stagingId: "",
      collection,
      revision: same.revision,
      subjects: same.subjects,
      unchanged: true,
      message: same.message ?? `${what} is unchanged since ${same.revision || "its last publish"}`,
    };
  }
  const stagingId = stagingIdOf(summary);

  stage(`publishing ${what}`);
  const publishJob = await deps.api.publish(scope, { provider: providerId, staging_id: stagingId });
  deps.trackJob?.({ jobId: publishJob.job_id, label: `Publish ${what}`, derivedKey: publishJob.derived_key });
  await pollToTerminal(deps, publishJob.job_id, PUBLISH_TIMEOUT_MS, `the publish of ${what}`, stage);

  const outcome = (await deps.api.readJson(scope, publishJob.derived_key)) as Record<string, unknown> | null;
  return {
    stagingId,
    collection: typeof outcome?.collection === "string" ? outcome.collection : collection,
    revision: typeof outcome?.revision === "string" ? outcome.revision : "",
    subjects: Array.isArray(outcome?.subjects) ? outcome!.subjects.map(String) : [],
  };
}

async function pollToTerminal(
  deps: CollectionRequestDeps,
  jobId: string,
  timeoutMs: number,
  what: string,
  stage: (s: string) => void,
): Promise<void> {
  const wait = deps.wait ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const now = deps.now ?? (() => Date.now());
  const startedAt = now();
  for (;;) {
    const st = await deps.api.jobStatus(jobId);
    if (st.status === "done") return;
    if (st.status === "error" || st.status === "cancelled") {
      throw new CollectionRequestError(`${what} ${st.status}${st.error ? `: ${st.error}` : ""}`);
    }
    if (now() - startedAt >= timeoutMs) {
      // Not cancelled: a job that does finish still stages its result, and the
      // staged list is where it will turn up.
      throw new CollectionRequestError(
        `${what} is still ${st.status} after ${Math.round(timeoutMs / 60000)} min. It was not cancelled; ` +
          "if it finishes, its result appears under Staged, not published.",
      );
    }
    if (st.stage) stage(st.stage);
    await wait(POLL_INTERVAL_MS);
  }
}
