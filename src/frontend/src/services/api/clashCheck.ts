// The clash-check routes: `POST /api/scopes/{scope}/clash-check`, `POST .../clash-detail`.
//
// A clash check identifies the joints in a loaded model's SOURCE -- never its GLB, which is
// triangles with no members -- and groups them by type (`ada.clash/result@1`,
// `src/ada/clash/result.py`). A clash-detail hand-off re-derives a chosen group's joints from the
// CACHED result (same options hash, so the ids are stable) and calls a registered spec's builder
// on the capability it advertises. Both are ordinary worker jobs, polled through the SAME
// `/convert/{job_id}` route every other job in this viewer uses (`./conversion`'s
// `convertStatus`) -- there is no separate clash job-status endpoint (Phase 6 brief).
//
// This module returns WIRE shapes only (snake_case, exactly what the route sends);
// `state/clashCheckStore.ts` is the one place that parses them into the model the panel reads,
// the same Wire/model split `services/api/assets.ts` + `@/assets/*` already hold.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";
import { filesApi } from "./files";

/** One registered spec that could detail a joint, exactly as the result document names it.
 *
 *  `capability` ABSENT (`null`/`undefined`) means a BUILT-IN spec that runs in core's own
 *  default pool -- it needs no live worker, so the panel offers it unconditionally
 *  (`ada/clash/builtin_specs.py`: "THEY CARRY NO CAPABILITY. A built-in spec runs wherever core
 *  runs ... so its capability is `None` and the panel offers it without asking a heartbeat.").
 *  `capability` PRESENT names the pool an out-of-tree spec is routed to; core never learns which
 *  package registered it (Decision 1's "capabilities present, never provider id" convention). */
export interface WireClashApplicableSpec {
  readonly spec: string;
  readonly capability?: string | null;
  readonly tags?: readonly string[];
  readonly priority?: number;
}

export interface WireClashJointMember {
  readonly name: string;
  /** `MemberKind.name` -- `"BEAM" | "PLATE"`, core vocabulary only. */
  readonly kind: string;
  readonly guid?: string | null;
  /** The section FAMILY (`section.type.value.upper()`, e.g. `"I"`, `"BOX"`) -- never the full
   *  profile designation, which no spec compares against. */
  readonly section?: string | null;
  /** Column | Girder | Brace for a beam; absent for a plate. */
  readonly member_type?: string | null;
}

export interface WireClashJoint {
  readonly id: string;
  readonly centre: readonly [number, number, number];
  /** Which PASS found this joint (`ada/clash/passes.py`). Absent in a document written before
   *  joints carried their producer, where core's beam pass was the only one there. */
  readonly origin?: string;
  /** What a geometric pass measured at the contact; absent for a pass that works on axes. */
  readonly contact?: Readonly<Record<string, unknown>> | null;
  readonly members: readonly WireClashJointMember[];
  readonly type_key: string;
  readonly type_label: string;
  readonly applicable?: readonly WireClashApplicableSpec[];
}

export interface WireClashGroup {
  readonly type_key: string;
  readonly type_label: string;
  readonly count: number;
  readonly joint_ids: readonly string[];
  readonly applicable?: readonly WireClashApplicableSpec[];
}

/** `ada.clash/result@1` on the wire. `counts` OMITS what was not measured -- a pass that could
 *  not run (no CAD backend for the plate passes, e.g.) leaves its key absent rather than zeroed,
 *  so "no plate joints" and "plate joints were never looked for" stay distinguishable. `members`
 *  is always present when the check ran at all, which is what lets the panel tell "this source
 *  has no members" (`members === 0`, a normal, successful answer) apart from every other empty
 *  state. */
export interface WireClashResult {
  readonly schema: string;
  readonly source_key: string;
  readonly source_sha256?: string | null;
  readonly options: Readonly<Record<string, unknown>>;
  readonly counts: Readonly<Record<string, number>>;
  readonly joints: readonly WireClashJoint[];
  readonly groups: readonly WireClashGroup[];
  readonly provenance: Readonly<Record<string, unknown>>;
  readonly warnings?: readonly string[];
  /** Every pass the check knew about, run or not. What the producer filter is built from. */
  readonly passes?: readonly WireClashPass[];
}

/** Core's own options -- tolerances and a subtree scope. No provider option ever rides in this
 *  document (`ada/clash/identify.py`'s `ClashOptions`). */
export interface ClashCheckOptions {
  readonly out_of_plane_tol?: number;
  readonly point_tol?: number;
  readonly root?: string | null;
  readonly include_plate_joints?: boolean;
  /** Which registered PASSES to run, by name (`ada/clash/passes.py`). Omitted means every pass
   *  core can run by itself; a capability-bearing pass -- one a plugin contributed, routed to the
   *  pool that carries it -- is opt-in, because a pass routed to a pool that is not there would
   *  make every default check report a failure nobody asked for.
   *
   *  Pass NAMES, never a provider id: core never learns which package contributed one, the same
   *  convention `applicable`'s `capability` already follows. */
  readonly passes?: readonly string[];
}

/** One pass the check knew about, and what became of it. Present for passes that were available
 *  and NOT selected too: "not run" and "found nothing" are different answers, and the panel
 *  offers the difference as a checkbox. */
export interface WireClashPass {
  readonly name: string;
  readonly ran: boolean;
  readonly found?: number;
  readonly reason?: string;
  /** The pool that can run it, or absent for one core runs anywhere. Lets the panel say WHY a
   *  pass is unavailable rather than merely that it is. */
  readonly capability?: string | null;
}

export interface ClashCheckResponse {
  readonly job_id: string | null;
  readonly derived_key: string;
  /** `true`: the result already sits at `derived_key` -- nothing was enqueued (Decision 3's "a
   *  repeat is not a job", the same convention `assetsApi.buildAssetNode`'s `cached` follows). */
  readonly cached: boolean;
}

export interface ClashDetailResponse {
  readonly job_id: string | null;
  /** The RESULT document (`…/result.stats.json`) -- the produced-joints take-off, not a model. */
  readonly derived_key: string;
  /** The produced GLB. This, never `derived_key`, is what the scene loads: asking the converter
   *  for the stats json answers 415 Unsupported Media Type, which is how the viewer reported a
   *  detail run that had in fact succeeded. */
  readonly glb_key?: string;
  readonly cached?: boolean;
}

function checkBase(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/clash-check`;
}
function detailBase(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/clash-detail`;
}

export const clashCheckApi = {
  async runClashCheck(
    scope: ScopeUrl,
    body: { source_key: string; options?: ClashCheckOptions },
  ): Promise<ClashCheckResponse> {
    const r = await authedFetch(checkBase(scope), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_key: body.source_key, options: body.options ?? {} }),
    });
    return jsonOrThrow<ClashCheckResponse>(r, `runClashCheck(${body.source_key})`);
  },

  async runClashDetail(
    scope: ScopeUrl,
    body: { result_key: string; joint_ids: readonly string[]; spec: string; options?: Record<string, unknown> },
  ): Promise<ClashDetailResponse> {
    const r = await authedFetch(detailBase(scope), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        result_key: body.result_key,
        joint_ids: body.joint_ids,
        spec: body.spec,
        options: body.options ?? {},
      }),
    });
    return jsonOrThrow<ClashDetailResponse>(r, `runClashDetail(${body.spec})`);
  },

  /** The result document at `key` -- gzip-at-rest JSON through the blob route, the same pattern
   *  as `assetsApi.getBuildSummary`: `fetch` decompresses it before `.json()` ever runs. */
  async getClashResult(scope: ScopeUrl, key: string): Promise<unknown> {
    const r = await authedFetch(filesApi.blobUrl(scope, key));
    return jsonOrThrow<unknown>(r, `getClashResult(${key})`);
  },

  /** The SOURCE file's bytes, for a check that runs in the browser.
   *
   *  Bytes rather than a URL handed to the worker: the blob route is authed, and a wasm worker
   *  streaming a URL directly carries no credentials. Where a presigned URL exists the scan can
   *  stream it through OPFS instead (`nativeIfcMemberScanStreaming`) and never materialise this. */
  async getSourceBytes(scope: ScopeUrl, key: string): Promise<ArrayBuffer> {
    const r = await authedFetch(filesApi.blobUrl(scope, key));
    if (!r.ok) throw new Error(`getSourceBytes(${key}) failed: ${r.status}`);
    return r.arrayBuffer();
  },

  /** A detail run's take-off (`…/result.stats.json`): `{joints: {count, by_type, items}, skipped?}`.
   *  The same blob fetch as the result document -- what differs is which document it is, and that
   *  is the caller's business, not the transport's. */
  async getDetailStats(scope: ScopeUrl, key: string): Promise<unknown> {
    const r = await authedFetch(filesApi.blobUrl(scope, key));
    return jsonOrThrow<unknown>(r, `getDetailStats(${key})`);
  },

  /** Which SEARCHES a check could run: core's own passes, unioned with whatever a live pool
   *  currently advertises on its heartbeat.
   *
   *  Read BEFORE the first run, which is the whole point of it. A pass contributed by a plugin
   *  would otherwise be discoverable only by seeing one in a result -- a checkbox that appears
   *  after you have already managed to do the thing it turns on.
   *
   *  A pass naming a `capability` runs only on a pool advertising it; core's answer `null` and
   *  run anywhere. Which package contributed one is never reported -- the capability is the whole
   *  of what core knows, the convention `applicable` already follows. */
  async listPasses(scope: ScopeUrl): Promise<readonly WireClashPassSpec[]> {
    const r = await authedFetch(`${runtime.apiBase}/scopes/${scope}/clash-check/passes`);
    const body = await jsonOrThrow<{ passes?: readonly WireClashPassSpec[] }>(r, "listPasses");
    return body.passes ?? [];
  },

  /** The capabilities a connection spec could be routed to right now -- the live union behind
   *  `isSpecAvailable`. A capability absent from this set has no pool serving it. */
  async listLiveConnectionCapabilities(scope: ScopeUrl): Promise<ReadonlySet<string>> {
    const r = await authedFetch(`${runtime.apiBase}/scopes/${scope}/clash-check/connection-specs`);
    const body = await jsonOrThrow<{ connection_specs?: readonly { capability?: string | null }[] }>(
      r,
      "listLiveConnectionCapabilities",
    );
    const out = new Set<string>();
    for (const spec of body.connection_specs ?? []) {
      if (typeof spec.capability === "string" && spec.capability.trim()) out.add(spec.capability);
    }
    return out;
  },
};

/** One pass the deployment could run, as the listing route describes it. Distinct from
 *  `WireClashPass`, which is what a RESULT says became of a pass on one particular run. */
export interface WireClashPassSpec {
  readonly slug: string;
  readonly name: string;
  readonly label?: string;
  readonly needs_backend?: boolean;
  readonly priority?: number;
  readonly capability?: string | null;
  /** `"code"` for one core carries, `"live"` for one a pool advertises -- `merge_catalog_specs`'
   *  own vocabulary, surfaced so the panel can say where a pass came from. */
  readonly origin?: string;
}
