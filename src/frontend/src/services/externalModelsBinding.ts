// Pure scope->collection binding helpers for the external-model API.
//
// Split from `externalModels.ts` on purpose: that module imports `viewerApi`,
// which reaches `auth/oidc.ts`, which touches `sessionStorage` at module scope.
// Anything importing it therefore needs a DOM. These functions are pure, so
// keeping them here lets them be unit-tested under plain node — and lets a
// consumer resolve a binding it already holds without pulling the API client.

/** Settings key holding the scope -> collection binding. Under the reserved
 *  `public.` prefix so a non-admin's UI can read it; writes stay admin-only. */
export const EXTERNAL_MODELS_BINDING_KEY = "public.external_models.binding_map";

/** The provider assumed when a binding names only a collection. */
export const DEFAULT_EXTERNAL_MODEL_PROVIDER = "demo";

/** scope url -> `"<provider>:<collection>"`, or a bare `"<collection>"`. */
export type ExternalModelBindingMap = Record<string, string>;

/** Parse the stored setting. Missing or malformed reads as an empty map rather
 *  than throwing: an unbound deployment is the normal case, and a panel must
 *  not fail to render because nobody has bound anything yet. */
export function parseBindingMap(raw: string | null): ExternalModelBindingMap {
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
  const out: ExternalModelBindingMap = {};
  for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
    if (typeof v === "string" && v) out[k] = v;
  }
  return out;
}

/** The binding for one scope, or null. Separate from the read so a consumer can
 *  hold the map and resolve many scopes without re-fetching the setting. */
export function bindingFor(
  map: ExternalModelBindingMap,
  scope: string,
): { provider: string; collection: string } | null {
  const raw = map[scope];
  if (!raw) return null;
  const idx = raw.indexOf(":");
  // A bare value is a collection on the default provider — the shape a
  // single-provider deployment naturally writes.
  if (idx < 0) return { provider: DEFAULT_EXTERNAL_MODEL_PROVIDER, collection: raw };
  // Only the FIRST colon separates, so a collection name may contain one.
  const provider = raw.slice(0, idx);
  const collection = raw.slice(idx + 1);
  // A half-written binding must read as unbound, not bind to an empty name.
  if (!provider || !collection) return null;
  return { provider, collection };
}

/** The extra <option> a bound collection <select> needs, or null.
 *
 *  A <select> whose `value` matches no <option> falls back to rendering its
 *  first one, so a bound scope reads as "— none —" until the collection list
 *  arrives. Collections are fetched on demand, so that is the state on first
 *  paint — the binding looked unset until the dropdown was opened.
 *
 *  `known` is `undefined` while the list has not been fetched and `[]` once it
 *  has and came back empty. The distinction matters: only a loaded list can say
 *  a binding is stale, so an unfetched one renders the value plainly rather
 *  than accusing it of being missing.
 */
export function boundCollectionOption(
  collection: string,
  known: readonly { id: string }[] | undefined,
): { value: string; label: string } | null {
  if (!collection) return null;
  if (known?.some((c) => c.id === collection)) return null;
  return {
    value: collection,
    label: known ? `${collection} (not in list)` : collection,
  };
}
