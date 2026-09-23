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
}

/** Core's own options -- tolerances and a subtree scope. No provider option ever rides in this
 *  document (`ada/clash/identify.py`'s `ClashOptions`). */
export interface ClashCheckOptions {
  readonly out_of_plane_tol?: number;
  readonly point_tol?: number;
  readonly root?: string | null;
  readonly include_plate_joints?: boolean;
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

  /** A detail run's take-off (`…/result.stats.json`): `{joints: {count, by_type, items}, skipped?}`.
   *  The same blob fetch as the result document -- what differs is which document it is, and that
   *  is the caller's business, not the transport's. */
  async getDetailStats(scope: ScopeUrl, key: string): Promise<unknown> {
    const r = await authedFetch(filesApi.blobUrl(scope, key));
    return jsonOrThrow<unknown>(r, `getDetailStats(${key})`);
  },

  // TODO(clash-check hand-off, Phase 6 §Decision 10 item 4): core is meant to grow a live
  // `connection_specs` union -- the heartbeat-derived list of capabilities a worker pool is
  // CURRENTLY advertising (the same union `advertised_specs("connection_specs")` folds
  // server-side). Once that route exists, add a `listLiveConnectionCapabilities(scope)` call
  // here and wire it into `state/clashCheckStore.ts`'s `isSpecAvailable`. Until then there is no
  // way to tell "this capability has no live pool" apart from "nobody has checked yet", so this
  // client deliberately exposes nothing for it and the store treats every capability as
  // available rather than hiding a spec that may in fact work (see `isSpecAvailable`'s doc).
};
