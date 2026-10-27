// Per-node world bounds a provider published (`ada.assets/node-bounds@1`, ingested by core on
// publish; `ada/comms/rest/db/node_bounds.py`), and the box query over them:
//
//   sets    GET   which (provider, collection) have bounds readable in this scope, with the frame
//                 their boxes are in
//   search  POST  the published nodes in a box -- loaded in the viewer or not -- folded to the
//                 topmost nodes that stand for whole levels
//
// The box is in the providers' MODEL frame (Z up, their geometry's units): the viewer subtracts its
// scene offset first (`utils/bboxSelection/boxRequest.ts`).

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

/** `[min_x, min_y, min_z, max_x, max_y, max_z]`. */
export type BoundsBox = readonly [number, number, number, number, number, number];

export interface BoundsFrame {
  readonly up?: string;
  readonly units?: string | null;
  readonly note?: string;
}

export interface BoundsSet {
  readonly scope: string;
  readonly provider: string;
  readonly collection: string;
  readonly revision: string | null;
  readonly rows: number;
  readonly frame: BoundsFrame;
  readonly ingested_at: string | null;
}

export interface BoundsHit {
  readonly scope: string;
  readonly provider: string;
  readonly collection: string;
  readonly node_id: string;
  readonly parent_id: string | null;
  /** The published subject a request or load of the node goes through; never null in a hit. */
  readonly subject: string;
  readonly label: string | null;
  readonly full_name: string | null;
  readonly kind: string | null;
  readonly depth: number | null;
  readonly bbox: BoundsBox;
}

export interface BoundsSearchQuery {
  readonly box: { readonly min: readonly [number, number, number]; readonly max: readonly [number, number, number] };
  /** `partial`: every leaf the box meets; `within`: every node inside it. */
  readonly mode: "partial" | "within";
  readonly collections?: readonly string[];
  readonly providers?: readonly string[];
  readonly limit?: number;
}

export interface BoundsSearchResponse {
  readonly sets: readonly BoundsSet[];
  readonly hits: readonly BoundsHit[];
  readonly mode: "partial" | "within";
  /** Why the answer is incomplete (too many nodes in the box), else null. */
  readonly truncated: string | null;
  readonly candidates: number;
  readonly took_ms: number;
}

function boundsBase(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/assets/bounds`;
}

export const nodeBoundsApi = {
  async sets(scope: ScopeUrl): Promise<BoundsSet[]> {
    const r = await authedFetch(boundsBase(scope));
    return (await jsonOrThrow<{ sets: BoundsSet[] }>(r, "boundsSets")).sets;
  },

  async search(scope: ScopeUrl, query: BoundsSearchQuery, signal?: AbortSignal): Promise<BoundsSearchResponse> {
    const r = await authedFetch(`${boundsBase(scope)}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(query),
      signal,
    });
    return jsonOrThrow(r, "boundsSearch");
  },
};
