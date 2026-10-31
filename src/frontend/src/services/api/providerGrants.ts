// Provider grants: the admin's map from scope to the provider-store datasets it may read, and the
// read-only list a scope shows (`ada/comms/rest/routes/admin_provider_grants.py`; the scope route
// beside the other `/scopes/{s}` ones). The shapes and what they mean are in `@/assets/providerGrants`.

import { runtime } from "@/runtime/config";
import type { ProviderGrant, ProviderGrantInput } from "@/assets/providerGrants";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const providerGrantsApi = {
  /** Admin: the grants, narrowed by what is given. A `collection` also returns the provider-wide
   *  grants that cover it. */
  async adminProviderGrants(
    filter: { target_scope?: string; provider?: string; collection?: string } = {},
  ): Promise<ProviderGrant[]> {
    const q = new URLSearchParams();
    if (filter.target_scope) q.set("target_scope", filter.target_scope);
    if (filter.provider) q.set("provider", filter.provider);
    if (filter.collection) q.set("collection", filter.collection);
    const r = await authedFetch(`${runtime.apiBase()}/admin/provider-grants${q.toString() ? `?${q}` : ""}`);
    return (await jsonOrThrow<{ grants: ProviderGrant[] }>(r, "adminProviderGrants")).grants;
  },

  /** Admin: make a grant, or replace the permissions of the one there. */
  async adminSetProviderGrant(body: ProviderGrantInput): Promise<unknown> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/provider-grants`, json("PUT", body));
    return jsonOrThrow(r, "adminSetProviderGrant");
  },

  /** Admin: the Access tab's checkboxes -- each collection in `grant` becomes readable by the scope
   *  (attributes and geometry), each in `revoke` loses its collection grant, in one transaction. */
  async adminSetProviderGrantCollections(body: {
    target_scope: string;
    provider: string;
    grant: readonly string[];
    revoke: readonly string[];
  }): Promise<{ granted: number; revoked: number; grants: ProviderGrant[] }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/provider-grants/collections`, json("POST", body));
    return jsonOrThrow(r, "adminSetProviderGrantCollections");
  },

  /** Admin: revoke. Immediate: access is checked on every request. */
  async adminRevokeProviderGrant(body: {
    target_scope: string;
    provider: string;
    collection: string | null;
  }): Promise<unknown> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/provider-grants`, json("DELETE", body));
    return jsonOrThrow(r, "adminRevokeProviderGrant");
  },

  /** What an admin granted this scope, for the Sources tab. */
  async scopeProviderGrants(scope: ScopeUrl): Promise<ProviderGrant[]> {
    const r = await authedFetch(`${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/provider-grants`);
    return (await jsonOrThrow<{ grants: ProviderGrant[] }>(r, "scopeProviderGrants")).grants;
  },
};
