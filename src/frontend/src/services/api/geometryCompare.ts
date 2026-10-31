// Two providers' tessellations of the same elements, compared -- the `geometry-compare` schedule kind
// (`ada/comms/rest/routes/geometry_compare.py`). Admin only: the gallery of what did not pass.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow } from "./client";

export type CompareVerdict = "pass" | "uncertain" | "fail" | "missing";

export interface CompareSide {
  subject?: string;
  node?: string;
  revision?: string;
  summary?: { name: string; bbox_min: number[]; bbox_max: number[]; volume: number; area: number; triangles: number };
  [key: string]: unknown;
}

/** One element's current verdict (`geometry_compare_elements`). */
export interface CompareItem {
  provider_a: string;
  provider_b: string;
  collection: string;
  key: string;
  name: string | null;
  verdict: CompareVerdict;
  reason: string | null;
  metrics: Record<string, unknown>;
  a: CompareSide | null;
  b: CompareSide | null;
  picture_a: string | null;
  picture_b: string | null;
  compared_at: string | null;
}

export interface CompareQuery {
  providerA: string;
  providerB?: string;
  collections?: string[];
  verdicts?: CompareVerdict[];
  q?: string;
  sort?: string;
  limit?: number;
  offset?: number;
}

export interface ComparePage {
  items: CompareItem[];
  total: number;
  counts: Partial<Record<CompareVerdict, number>>;
}

export function compareQueryString(query: CompareQuery): string {
  const q = new URLSearchParams({ provider_a: query.providerA });
  if (query.providerB) q.set("provider_b", query.providerB);
  for (const c of query.collections ?? []) q.append("collection", c);
  for (const v of query.verdicts ?? []) q.append("verdict", v);
  if (query.q?.trim()) q.set("q", query.q.trim());
  if (query.sort) q.set("sort", query.sort);
  q.set("limit", String(query.limit ?? 24));
  q.set("offset", String(query.offset ?? 0));
  return q.toString();
}

export const geometryCompareApi = {
  async adminGeometryCompareItems(query: CompareQuery): Promise<ComparePage> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/geometry-compare/items?${compareQueryString(query)}`);
    return jsonOrThrow(r, "adminGeometryCompareItems");
  },

  /** A picture's URL (admin route, served from the provider store). */
  pictureUrl(key: string): string {
    return `${runtime.apiBase()}/admin/geometry-compare/picture?${new URLSearchParams({ key })}`;
  },

  /** A picture as an object URL (the route needs the session's auth, which an <img src> cannot send). */
  async pictureObjectUrl(key: string): Promise<string> {
    const r = await authedFetch(geometryCompareApi.pictureUrl(key));
    if (!r.ok) throw new Error(`picture ${r.status}`);
    return URL.createObjectURL(await r.blob());
  },
};
