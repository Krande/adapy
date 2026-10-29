// The relations index: typed, directed relations between published nodes that any provider stages
// beside its tree (`ada.assets/node-relations@1`), read by node full name
// (`POST /api/scopes/{scope}/assets/relations`).
//
// Provider-neutral: a relation is a word core defines (`serves_system`, `flows_to`, ...) and the
// props it documents. The answer holds every provider's set for the collection.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

export interface NodeRelation {
  provider: string;
  from_node_id: string;
  from_full_name: string | null;
  relation: string;
  to_node_id: string | null;
  to_full_name: string | null;
  props: Record<string, unknown>;
}

export interface NodeRelationsAnswer {
  indexed: boolean;
  relations: NodeRelation[];
  token: string | null;
}

/** Names per request: the server's `LOOKUP_MAX`. */
export const RELATIONS_LOOKUP_MAX = 5000;

/** A system a collection's relations name (`serves_system` targets). */
export interface SystemSummary {
  provider: string;
  name: string;
  /** `duct`, `piping`, ... -- core's system kinds. */
  kind: string | null;
  /** The `IfcDistributionSystemEnum` value the provider gave. */
  system_type: string | null;
  members: number;
  runs: number;
}

/** Where a member can be loaded from: a provider's site (subject) and the node in it. */
export interface MemberSite {
  provider: string;
  subject: string;
  node_id: string;
}

export interface SystemMember {
  full_name: string;
  node_id: string;
  run: string | null;
  sites: MemberSite[];
}

function base(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/assets`;
}

export const nodeRelationsApi = {
  /** The systems of `collection`, largest first. */
  async systems(scope: ScopeUrl, collection: string): Promise<{ indexed: boolean; systems: SystemSummary[] }> {
    const r = await authedFetch(`${base(scope)}/systems?collection=${encodeURIComponent(collection)}`);
    return jsonOrThrow(r, "systems");
  },

  /** The elements that serve `system` in `collection`. */
  async systemMembers(
    scope: ScopeUrl,
    collection: string,
    system: string,
  ): Promise<{ members: SystemMember[]; truncated: boolean }> {
    const q = new URLSearchParams({ collection, system });
    const r = await authedFetch(`${base(scope)}/systems/members?${q}`);
    return jsonOrThrow(r, "systemMembers");
  },

  /** The relations FROM the nodes of `collection` named by `fullNames`, in requests of at most
   *  {@link RELATIONS_LOOKUP_MAX} names. */
  async relationsFrom(
    scope: ScopeUrl,
    collection: string,
    fullNames: readonly string[],
    signal?: AbortSignal,
  ): Promise<NodeRelationsAnswer> {
    const out: NodeRelationsAnswer = { indexed: false, relations: [], token: null };
    for (let i = 0; i < Math.max(fullNames.length, 1); i += RELATIONS_LOOKUP_MAX) {
      const r = await authedFetch(`${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/assets/relations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collection, full_names: fullNames.slice(i, i + RELATIONS_LOOKUP_MAX) }),
        signal,
      });
      const page = await jsonOrThrow<NodeRelationsAnswer>(r, "relationsFrom");
      out.indexed ||= page.indexed;
      out.token ??= page.token;
      out.relations.push(...page.relations);
      if (!page.indexed) break;
    }
    return out;
  },
};
