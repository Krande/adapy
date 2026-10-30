// Provider grants: which scopes may read which of the provider store's datasets
// (`ada/comms/rest/routes/admin_provider_grants.py`, and `GET /scopes/{s}/provider-grants`).
//
// Pure, on purpose, as `@/services/assetScopeCollections` is: the labels and the request bodies are
// testable under plain node, and the client in `@/services/api/providerGrants` only does the fetch.
//
// A DATASET is `(provider, collection)`; `collection: null` is every collection of the provider. A
// grant gives a TARGET SCOPE -- a storage prefix: `shared`, `projects/<uuid>`, `users/<sub>` -- the
// right to read a dataset's attributes and/or its geometry. The `request` flag exists on the wire
// but is never offered here: asking a provider for data is an admin operation, always.

/** One grant, as the admin API lists it. */
export interface ProviderGrant {
  id: string;
  target_scope: string;
  provider: string;
  collection: string | null;
  attributes: boolean;
  geometry: boolean;
  request: boolean;
  granted_at: string | null;
  granted_by: string | null;
  note: string | null;
}

/** What a grant is made or replaced with. `request` is always false from here. */
export interface ProviderGrantInput {
  target_scope: string;
  provider: string;
  collection: string | null;
  attributes: boolean;
  geometry: boolean;
  request: false;
  note?: string | null;
}

/** The body of a `PUT /admin/provider-grants`: `request` is pinned off, whatever was stored. */
export function grantInput(
  g: Pick<ProviderGrant, "target_scope" | "provider" | "collection"> & Partial<Pick<ProviderGrant, "attributes" | "geometry" | "note">>,
  patch: Partial<Pick<ProviderGrantInput, "attributes" | "geometry" | "note">> = {},
): ProviderGrantInput {
  const note = patch.note !== undefined ? patch.note : g.note;
  return {
    target_scope: g.target_scope,
    provider: g.provider,
    collection: g.collection,
    attributes: patch.attributes ?? g.attributes ?? true,
    geometry: patch.geometry ?? g.geometry ?? true,
    request: false,
    ...(note !== undefined && note !== null && note.trim() ? { note: note.trim() } : {}),
  };
}

/** The scope a grant row targets, as the admin API names it. */
export const SHARED_TARGET = "shared";
export const projectTarget = (projectId: string): string => `projects/${projectId}`;
export const userTarget = (sub: string): string => `users/${sub}`;

/** A target scope in words: "Shared", the project's name (from the admin project list; the id when
 *  it is gone from it), or the user. */
export function targetLabel(target: string, projectNames: ReadonlyMap<string, string>): string {
  if (target === SHARED_TARGET) return "Shared";
  if (target.startsWith("projects/")) {
    const id = target.slice("projects/".length);
    return `Project · ${projectNames.get(id) ?? id}`;
  }
  if (target.startsWith("users/")) return `User · ${target.slice("users/".length)}`;
  return target;
}

/** A grant's collection in words. */
export function collectionLabel(collection: string | null): string {
  return collection === null ? "All collections" : collection;
}
