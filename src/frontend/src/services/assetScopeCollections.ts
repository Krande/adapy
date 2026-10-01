// Which of an asset provider's collections may be REQUESTED in each scope.
//
// Pure, on purpose, for the reason `externalModelsBinding.ts` is: the API
// client reaches `auth/oidc.ts`, which touches `sessionStorage` at module
// scope, so anything importing it needs a DOM. Nothing here imports it. That
// keeps the reader testable under plain node, and lets a plugin resolve a value
// it already holds without pulling the client in. Exported to plugins as part
// of plugin API 1.8.0 -- see `plugins/registry.ts`.
//
// THE SHAPE, stored as JSON TEXT under one public setting (the admin settings
// route stores `str(value)`):
//
//   public.assets.scope_collections = {
//     "<scope url>": { "<provider id>": ["<collection>", ...] }
//   }
//
// A GRANT, NOT A BINDING. `public.external_models.binding_map` says which ONE
// collection a scope shows; this says which of a provider's collections a
// scope may ask for. Two questions, two settings.
//
// A PICKER FILTER, NOT A PERMISSION. A provider's request dialog narrows its
// choice by this list; nothing on the server refuses a request for a
// collection left out. Published assets are not filtered by it either -- they
// are already scope-gated, and hiding some would make the tree lie about what
// the scope holds.
//
// MISSING IS NOT EMPTY. A scope, or a provider within it, that nobody has
// configured is UNRESTRICTED -- every scope behaves as it did before this
// existed. A stored `[]` enables NOTHING. `enabledFor` returns `null` for the
// first and `[]` for the second, and the type says so, so a caller that forgets
// the difference fails to compile rather than granting everything.
//
// SPELLING. A collection is stored exactly as the provider ADVERTISES it, and
// core never translates: only the provider knows whether two spellings are one
// collection.

/** The public setting holding the per-scope enabled collections. */
export const ASSET_SCOPE_COLLECTIONS_KEY = "public.assets.scope_collections";

/** The enabled collections for one provider in one scope. `null` is
 *  UNRESTRICTED (nothing configured); `[]` is NOTHING ENABLED. */
export type EnabledCollections = readonly string[] | null;

/** scope url -> provider id -> collections, as stored. */
export type ScopeCollectionsMap = Readonly<
  Record<string, Readonly<Record<string, readonly string[]>>>
>;

/** Parse the stored setting. Accepts the JSON text the settings route returns,
 *  or an already-decoded object.
 *
 *  Missing or malformed reads as an EMPTY MAP -- every scope unrestricted --
 *  rather than throwing: an unconfigured deployment is the normal case, and a
 *  hand-edited row must not take a dialog down.
 *
 *  Entries that are not the known shape are dropped, not guessed at. A provider
 *  entry that is not an array is dropped WHOLE, which leaves that provider
 *  unrestricted: the direction that offers too much rather than nothing, for a
 *  list that only narrows a picker. */
export function parseScopeCollections(raw: unknown): ScopeCollectionsMap {
  if (!raw) return {};
  let parsed: unknown = raw;
  if (typeof raw === "string") {
    try {
      parsed = JSON.parse(raw);
    } catch {
      return {};
    }
  }
  if (!isRecord(parsed)) return {};
  const out: Record<string, Record<string, string[]>> = {};
  for (const [scope, providers] of Object.entries(parsed)) {
    if (!isRecord(providers)) continue;
    const row: Record<string, string[]> = {};
    for (const [provider, list] of Object.entries(providers)) {
      if (!Array.isArray(list)) continue;
      row[provider] = uniq(
        list.filter((c): c is string => typeof c === "string" && c.trim().length > 0),
      );
    }
    out[scope] = row;
  }
  return out;
}

/** The enabled collections for `provider` in `scope`: `null` when that pair is
 *  unrestricted, otherwise the stored list (possibly empty). */
export function enabledFor(
  map: ScopeCollectionsMap,
  scope: string,
  provider: string,
): EnabledCollections {
  const list = map[scope]?.[provider];
  return list === undefined ? null : list;
}

/** Is `collection` enabled here? Always true when unrestricted. Compared
 *  EXACTLY: a provider that treats two spellings as one says so itself. */
export function isCollectionEnabled(enabled: EnabledCollections, collection: string): boolean {
  return enabled === null || enabled.includes(collection);
}

/** A copy of `map` with the `(scope, provider)` entry set to `enabled`.
 *
 *  `null` REMOVES the entry, making the pair unrestricted again, and a scope
 *  left with no provider entries is removed with it -- so resetting the last
 *  restriction leaves the setting as it was before anybody touched it, rather
 *  than an object of empty objects. `[]` is stored as `[]`. */
export function withEnabled(
  map: ScopeCollectionsMap,
  scope: string,
  provider: string,
  enabled: EnabledCollections,
): ScopeCollectionsMap {
  const out: Record<string, Record<string, readonly string[]>> = {};
  for (const [s, row] of Object.entries(map)) out[s] = { ...row };
  const row = out[scope] ?? {};
  if (enabled === null) {
    delete row[provider];
  } else {
    row[provider] = uniq(enabled);
  }
  if (Object.keys(row).length === 0) {
    delete out[scope];
  } else {
    out[scope] = row;
  }
  return out;
}

/** The stored form: JSON text, because the settings route stores a string. */
export function serialiseScopeCollections(map: ScopeCollectionsMap): string {
  return JSON.stringify(map);
}

// ---------------------------------------------------------------------------
// Which providers have collections, as the live plugin specs declare it.
//
// A backend plugin spec (GET /plugins) may carry two extra keys:
//
//   asset_provider_id        the asset provider its collections belong to
//   asset_collections_field  the name of the spec field listing them
//
// Core acts on neither beyond reading them here, and names no provider: the
// declaration travels with the worker that advertises it, so it is per machine
// and per deployment by construction. The field is expected to be one of the
// spec's union fields, so several workers serving one provider pool their
// lists before this sees them.
// ---------------------------------------------------------------------------

/** One asset provider whose collections a live plugin advertises. */
export interface AssetProviderCollections {
  providerId: string;
  /** Backend plugin ids declaring it, for the tab to say where the list came from. */
  pluginIds: readonly string[];
  /** Human titles of those plugins, where they gave one. */
  titles: readonly string[];
  /** The advertised collections, as spelled by the provider, sorted. */
  collections: readonly string[];
  /** How to ask for a fresh list, when a spec declares one: the plugin to run a
   *  job on and the options to send. `null` when nobody declares it. */
  refresh: AssetCollectionsRefresh | null;
  /** How to ask the provider for one collection, when a spec declares it. */
  request: AssetCollectionRequest | null;
  /** How to ask the provider for one NODE of a published collection, when a spec declares it. */
  nodeRequest: AssetNodeRequest | null;
}

/** A declared request: `asset_collection_request` on a plugin spec.
 *
 *  `{options, collection_option, label?}` -- the job options to send, and the
 *  name of the option that carries the chosen collection. Core adds the
 *  collection under that name and a `requested_at` stamp, and nothing else. The
 *  job is expected to STAGE what it fetched and return its `asset_staging_id`,
 *  which core then publishes under the provider's id. */
export interface AssetCollectionRequest {
  pluginId: string;
  options: Readonly<Record<string, unknown>>;
  collectionOption: string;
  label: string;
  /** The plugin's job needs an admin (its spec's EFFECTIVE `requires_admin`), so
   *  anyone else is told why rather than offered a button the server refuses. */
  requiresAdmin: boolean;
}

function parseRequest(pluginId: string, raw: unknown, requiresAdmin: boolean): AssetCollectionRequest | null {
  if (!pluginId || !isRecord(raw)) return null;
  const collectionOption = raw.collection_option;
  if (typeof collectionOption !== "string" || !collectionOption.trim()) return null;
  return {
    pluginId,
    options: isRecord(raw.options) ? { ...raw.options } : {},
    collectionOption: collectionOption.trim(),
    label: typeof raw.label === "string" && raw.label.trim() ? raw.label.trim() : "Request",
    requiresAdmin,
  };
}

/** A declared node request: `asset_node_request` on a plugin spec.
 *
 *  `{options, collection_option, node_option, label?}` -- a collection request that also names
 *  one node of it: core adds the node's id, as the browser shows it, under `node_option`. The id
 *  is the one the provider published the node under, so turning it back into whatever its source
 *  calls that node is the provider's job. Staged and published exactly like a collection. */
export interface AssetNodeRequest extends AssetCollectionRequest {
  nodeOption: string;
  /** `label_option`, when declared: core also sends the node's LABEL, as the tree shows it, under
   *  this option. For a provider that must find a node published under ANOTHER provider's id --
   *  an id it cannot turn back into anything of its own -- the label is the name to look for. */
  labelOption?: string;
  /** `max_nodes`: how many nodes ONE request may name. Several selected nodes are sent in batches
   *  of this size -- one job for many is what a provider that pays a heavy start per job (opening
   *  a design project) wants. Undeclared (absent here): 1, a job per node, which every provider can
   *  take. */
  maxNodes?: number;
}

function parseNodeRequest(pluginId: string, raw: unknown, requiresAdmin: boolean): AssetNodeRequest | null {
  const base = parseRequest(pluginId, raw, requiresAdmin);
  if (!base || !isRecord(raw)) return null;
  const nodeOption = raw.node_option;
  if (typeof nodeOption !== "string" || !nodeOption.trim()) return null;
  const labelOption = typeof raw.label_option === "string" && raw.label_option.trim() ? raw.label_option.trim() : undefined;
  const maxNodes = typeof raw.max_nodes === "number" && Number.isInteger(raw.max_nodes) && raw.max_nodes > 1 ? raw.max_nodes : null;
  return { ...base, nodeOption: nodeOption.trim(), ...(labelOption ? { labelOption } : {}), ...(maxNodes ? { maxNodes } : {}) };
}

/** A declared rescan: `asset_collections_refresh` on a plugin spec, which is the
 *  job options core sends to that plugin. Core adds only a `requested_at`
 *  stamp, so a second request is a second job rather than a cache hit. */
export interface AssetCollectionsRefresh {
  pluginId: string;
  options: Readonly<Record<string, unknown>>;
}

/** Read the provider -> collections links out of `GET /plugins` specs.
 *
 *  A spec whose named field is missing or not a list of strings is still
 *  listed, with no collections: the provider exists and says it has none right
 *  now, which is different from not being declared at all. Specs naming the
 *  same provider are merged. */
export function assetProviderCollections(
  specs: readonly unknown[],
): AssetProviderCollections[] {
  const byProvider = new Map<
    string,
    {
      pluginIds: string[];
      titles: string[];
      collections: Set<string>;
      refresh: AssetCollectionsRefresh | null;
      request: AssetCollectionRequest | null;
      nodeRequest: AssetNodeRequest | null;
    }
  >();
  for (const spec of specs) {
    if (!isRecord(spec)) continue;
    const providerId = spec.asset_provider_id;
    const field = spec.asset_collections_field;
    if (typeof providerId !== "string" || !providerId) continue;
    const entry = byProvider.get(providerId) ?? {
      pluginIds: [],
      titles: [],
      collections: new Set<string>(),
      refresh: null,
      request: null,
      nodeRequest: null,
    };
    const pluginId = typeof spec.id === "string" ? spec.id : typeof spec.slug === "string" ? spec.slug : "";
    if (pluginId && !entry.pluginIds.includes(pluginId)) entry.pluginIds.push(pluginId);
    // The first declaring spec wins: several workers advertising one plugin
    // carry the same declaration, and one rescan job is what is wanted.
    if (!entry.refresh && pluginId && isRecord(spec.asset_collections_refresh)) {
      entry.refresh = { pluginId, options: { ...spec.asset_collections_refresh } };
    }
    if (!entry.request) {
      entry.request = parseRequest(pluginId, spec.asset_collection_request, spec.requires_admin === true);
    }
    if (!entry.nodeRequest) {
      entry.nodeRequest = parseNodeRequest(pluginId, spec.asset_node_request, spec.requires_admin === true);
    }
    if (typeof spec.title === "string" && spec.title && !entry.titles.includes(spec.title)) {
      entry.titles.push(spec.title);
    }
    const listed = typeof field === "string" ? spec[field] : undefined;
    if (Array.isArray(listed)) {
      for (const c of listed) if (typeof c === "string" && c.trim()) entry.collections.add(c);
    }
    byProvider.set(providerId, entry);
  }
  return [...byProvider.entries()]
    .map(([providerId, e]) => ({
      providerId,
      pluginIds: e.pluginIds,
      titles: e.titles,
      collections: [...e.collections].sort(compare),
      refresh: e.refresh,
      request: e.request,
      nodeRequest: e.nodeRequest,
    }))
    .sort((a, b) => compare(a.providerId, b.providerId));
}

/** One checkbox in the tab. */
export interface CollectionChoice {
  collection: string;
  enabled: boolean;
  /** An online worker advertises it right now. */
  advertised: boolean;
}

/** The checkboxes for one `(scope, provider)`: every advertised collection,
 *  PLUS every enabled one nobody advertises right now.
 *
 *  A worker being offline is not a revocation. Rebuilding the list from the
 *  live advertisement alone would let the next save drop every grant served by
 *  a host that happened to be rebooting, silently. So a stored grant is always
 *  listed, and marked, and only an admin unticking it removes it.
 *
 *  Advertised first, in the provider's order; unadvertised grants after. */
export function collectionChoices(
  advertised: readonly string[],
  enabled: EnabledCollections,
): CollectionChoice[] {
  const on = new Set(enabled ?? []);
  const seen = new Set(advertised);
  const out: CollectionChoice[] = advertised.map((collection) => ({
    collection,
    enabled: enabled === null || on.has(collection),
    advertised: true,
  }));
  for (const collection of [...on].sort(compare)) {
    if (!seen.has(collection)) out.push({ collection, enabled: true, advertised: false });
  }
  return out;
}

/** `enabled` with `collection` switched on or off. A pair that was
 *  unrestricted becomes restricted to everything EXCEPT the one being switched
 *  off, over `advertised` -- the only reading of "untick one of all" that does
 *  not change anything else. */
export function toggleCollection(
  enabled: EnabledCollections,
  advertised: readonly string[],
  collection: string,
  on: boolean,
): readonly string[] {
  const base = enabled === null ? [...advertised] : [...enabled];
  const rest = base.filter((c) => c !== collection);
  return on ? uniq([...rest, collection]) : rest;
}

/** Providers the stored map mentions, whether or not a worker declares them now. */
export function storedProviders(map: ScopeCollectionsMap): string[] {
  const out = new Set<string>();
  for (const row of Object.values(map)) for (const p of Object.keys(row)) out.add(p);
  return [...out].sort(compare);
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function uniq(list: readonly string[]): string[] {
  return [...new Set(list)];
}

function compare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
}
