// What a collection offers for a new element to point at (`ada.assets/reference-data@1`, ingested by
// core on publish; `ada/comms/rest/db/reference_data.py`): every spec, spec component and material a
// provider listed, used by the model or not -- the SPRE / MATR values a macro may write.
//
//   sets   GET  which (provider, collection) have reference data readable in this scope
//   items  GET  one page of a set's entries, filtered, with how many elements use each one

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

export interface ReferenceSet {
  readonly scope: string;
  readonly provider: string;
  readonly collection: string;
  readonly revision: string | null;
  readonly rows: number;
  /** Entries per kind (`spec`, `spco`, `material`). */
  readonly counts: Readonly<Record<string, number>>;
  readonly ingested_at: string | null;
}

export interface ReferenceUsage {
  readonly count: number;
  /** Users per element type (GENSEC, PANEL ...). */
  readonly by_kind: Readonly<Record<string, number>>;
}

export interface ReferenceItem {
  readonly kind: string;
  /** Exactly what an element's reference attribute holds: what a macro writes after SPRE / MATR. */
  readonly ref: string;
  readonly name: string | null;
  /** The entry it belongs to: an SPCO's spec. */
  readonly owner: string | null;
  /** What the provider says it is for: section, plate, fitting, joint, material, other. */
  readonly use: string | null;
  readonly attrs: Readonly<Record<string, unknown>>;
  /** With `usage`: who uses it; null for a kind no attribute names. Absent without `usage`. */
  readonly used?: ReferenceUsage | null;
}

export interface ReferenceQuery {
  readonly provider: string;
  readonly collection: string;
  readonly kind?: string;
  readonly use?: string;
  readonly owner?: string;
  /** Of `ref`, case-sensitive. */
  readonly prefix?: string;
  /** In `ref`, name or attributes, any case. */
  readonly q?: string;
  readonly limit?: number;
  readonly offset?: number;
  readonly usage?: boolean;
}

export interface ReferencePage {
  readonly set: ReferenceSet | null;
  readonly items: readonly ReferenceItem[];
  readonly total: number;
  readonly facets: { readonly kinds?: Readonly<Record<string, number>>; readonly uses?: Readonly<Record<string, number>> };
  readonly next_offset: number | null;
  /** With `usage`: whether the collection has an attribute index to count in. */
  readonly usage: { readonly indexed: boolean; readonly provider?: string } | null;
  readonly took_ms: number;
}

function referenceBase(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/assets/reference`;
}

export const referenceDataApi = {
  async sets(scope: ScopeUrl): Promise<ReferenceSet[]> {
    const r = await authedFetch(referenceBase(scope));
    return (await jsonOrThrow<{ sets: ReferenceSet[] }>(r, "referenceSets")).sets;
  },

  async items(scope: ScopeUrl, query: ReferenceQuery, signal?: AbortSignal): Promise<ReferencePage> {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue;
      params.set(k, String(v));
    }
    const r = await authedFetch(`${referenceBase(scope)}/items?${params.toString()}`, { signal });
    return jsonOrThrow(r, "referenceItems");
  },
};
