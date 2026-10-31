// Provider grants: which scopes may read which of the provider store's datasets
// (`ada/comms/rest/routes/admin_provider_grants.py`, and `GET /scopes/{s}/provider-grants`). The
// ONLY control of which scope uses which provider collection: the per-scope collection matrix
// (`public.assets.scope_collections`) is migrated into grants at startup and no longer read.
//
// Pure, on purpose: the labels and the request bodies are testable under plain node, and the
// client in `@/services/api/providerGrants` only does the fetch.
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

// --- the Access tab's checkboxes: per scope, per provider, one per collection ---------------------
//
// A checkbox is READ access -- attributes and geometry. A collection grant with only one of the two
// (from an older rule, or set under "Every grant") still shows ticked: the scope reads something of
// it, and unticking revokes it. The provider-wide grant (`collection: null`) is its own checkbox, "All
// collections", which also covers collections published later.

/** What one scope may read of one provider, as the checkboxes show it. */
export interface ScopeProviderAccess {
  /** A provider-wide grant: every collection, the later ones included. */
  all: boolean;
  /** The collections it holds a read grant on, lower-case (as grants store them). */
  collections: ReadonlySet<string>;
}

const reads = (g: Pick<ProviderGrant, "attributes" | "geometry">): boolean => g.attributes || g.geometry;

export function scopeProviderAccess(
  grants: readonly ProviderGrant[],
  target: string,
  provider: string,
): ScopeProviderAccess {
  let all = false;
  const collections = new Set<string>();
  for (const g of grants) {
    if (g.target_scope !== target || g.provider !== provider || !reads(g)) continue;
    if (g.collection === null) all = true;
    else collections.add(g.collection.toLowerCase());
  }
  return { all, collections };
}

/** One checkbox: the collection as the provider advertises it (or as granted, when no worker
 *  advertises it now), whether the scope reads it, and whether it is advertised. */
export interface GrantChoice {
  collection: string;
  granted: boolean;
  advertised: boolean;
}

/** The checkboxes for one scope and provider: every advertised collection, plus every granted one
 *  no worker advertises right now (offline is not revocation), in project order by the caller. */
export function grantChoices(advertised: readonly string[], access: ScopeProviderAccess): GrantChoice[] {
  const seen = new Set<string>();
  const out: GrantChoice[] = [];
  for (const c of advertised) {
    const key = c.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ collection: c, granted: access.collections.has(key), advertised: true });
  }
  for (const key of access.collections) {
    if (!seen.has(key)) out.push({ collection: key, granted: true, advertised: false });
  }
  return out;
}

/** The batch body that ticks (`on`) or unticks `collections`, skipping the ones already so. */
export function grantDelta(
  choices: readonly GrantChoice[],
  collections: readonly string[],
  on: boolean,
): { grant: string[]; revoke: string[] } {
  const wanted = new Set(collections.map((c) => c.toLowerCase()));
  const changing = choices.filter((c) => wanted.has(c.collection.toLowerCase()) && c.granted !== on).map((c) => c.collection);
  return on ? { grant: changing, revoke: [] } : { grant: [], revoke: changing };
}
