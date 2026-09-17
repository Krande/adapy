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

/** What a scope is bound to, once parsed. */
export interface ExternalModelBinding {
  provider: string;
  collection: string;
  /** Models this scope should not see: model ids, plus any hand-written
   *  wildcard patterns. Empty means show all. See {@link isHidden}. */
  hide: string[];
}

/** The stored form of one binding.
 *
 *  THREE SHAPES, and all three are read. `"<collection>"` and
 *  `"<provider>:<collection>"` are what deployments already hold, and an
 *  object is what a binding with a model filter needs. Writing keeps the
 *  string where it still says everything -- see {@link serialiseBinding} --
 *  so adding this did not rewrite every existing entry into a longer form
 *  that means the same thing. */
export type StoredBinding =
  | string
  | { provider?: string; collection: string; hide?: string[] };

/** scope url -> its stored binding. */
export type ExternalModelBindingMap = Record<string, StoredBinding>;

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
    if (typeof v === "string" && v) {
      out[k] = v;
      continue;
    }
    // The object form. A row that is not one of the two known shapes is
    // dropped rather than guessed at: a half-understood binding pointing
    // somewhere unintended is worse than an unbound scope, which at least says
    // so on screen.
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const o = v as Record<string, unknown>;
      const collection = typeof o.collection === "string" ? o.collection : "";
      if (!collection) continue;
      out[k] = {
        provider: typeof o.provider === "string" ? o.provider : undefined,
        collection,
        hide: Array.isArray(o.hide)
          ? o.hide.map((p) => String(p).trim()).filter(Boolean)
          : undefined,
      };
    }
  }
  return out;
}

/** The stored form for a binding, as compact as it can honestly be.
 *
 *  A binding with no filter goes back as `"<provider>:<collection>"`, which is
 *  what every existing entry looks like. Only one carrying `hide` needs the
 *  object, so turning the filter on is the only thing that changes an entry's
 *  shape -- and turning it off changes it back. */
export function serialiseBinding(b: {
  provider: string;
  collection: string;
  hide?: string[];
}): StoredBinding {
  const hide = (b.hide ?? []).map((p) => p.trim()).filter(Boolean);
  if (hide.length === 0) return `${b.provider}:${b.collection}`;
  return { provider: b.provider, collection: b.collection, hide };
}

/** Does an entry carry a wildcard, and therefore mean a pattern? */
export function isPattern(entry: string): boolean {
  return entry.includes("*") || entry.includes("?");
}

/** Does `text` match a glob with `*` and `?`, case-insensitively?
 *
 *  ANCHORED, and a regex only internally: everything but the wildcards is
 *  escaped, so `*(511)*` matches a name containing parentheses rather
 *  than quietly meaning a capture group.
 *
 *  Only reached for entries that HAVE a wildcard -- see {@link isHidden}. */
export function matchesGlob(text: string, pattern: string): boolean {
  const p = pattern.trim();
  if (!p) return false;
  const hay = text.toLowerCase();
  const needle = p.toLowerCase();
  const escaped = needle.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  const rx = new RegExp(`^${escaped.replace(/\*/g, ".*").replace(/\?/g, ".")}$`);
  return rx.test(hay);
}

/** Should this model be hidden from the scope it was listed for?
 *
 *  TWO KINDS OF ENTRY, and which one applies is decided by the entry itself.
 *
 *  Without a wildcard it is a MODEL ID, matched EXACTLY. That is what the admin
 *  panel's checkbox list writes, and exactness is the whole reason: these ids
 *  nest -- `export-a.rvm~site` is a prefix of
 *  `export-a.rvm~site-one` -- so a substring rule would tick one
 *  site and hide two. Hiding a model nobody asked to hide is the failure mode
 *  worth designing against, because the model simply is not there and nothing
 *  says why.
 *
 *  With a wildcard it is a pattern, matched against the id, the name AND the
 *  description. That form is not written by the UI; it exists because a
 *  hand-edited `*draft*` keeps applying to models that do not exist yet,
 *  which a list of ticks cannot do. Both kinds live in one list, and the panel
 *  shows the patterns separately so they are never mistaken for a tick nobody
 *  can find. */
export function isHidden(
  model: { id?: string; name?: string; description?: string | null },
  entries: readonly string[],
): boolean {
  if (!entries.length) return false;
  const id = (model.id ?? "").toLowerCase();
  const fields = [model.id, model.name, model.description].filter(
    (f): f is string => typeof f === "string" && f.length > 0,
  );
  return entries.some((entry) => {
    const e = entry.trim();
    if (!e) return false;
    if (!isPattern(e)) return id !== "" && id === e.toLowerCase();
    return fields.some((f) => matchesGlob(f, e));
  });
}

/** The binding for one scope, or null. Separate from the read so a consumer can
 *  hold the map and resolve many scopes without re-fetching the setting. */
export function bindingFor(
  map: ExternalModelBindingMap,
  scope: string,
): ExternalModelBinding | null {
  const raw = map[scope];
  if (!raw) return null;

  if (typeof raw !== "string") {
    if (!raw.collection) return null;
    return {
      provider: raw.provider || DEFAULT_EXTERNAL_MODEL_PROVIDER,
      collection: raw.collection,
      hide: raw.hide ?? [],
    };
  }

  const idx = raw.indexOf(":");
  // A bare value is a collection on the default provider — the shape a
  // single-provider deployment naturally writes.
  if (idx < 0) {
    return { provider: DEFAULT_EXTERNAL_MODEL_PROVIDER, collection: raw, hide: [] };
  }
  // Only the FIRST colon separates, so a collection name may contain one.
  const provider = raw.slice(0, idx);
  const collection = raw.slice(idx + 1);
  // A half-written binding must read as unbound, not bind to an empty name.
  if (!provider || !collection) return null;
  return { provider, collection, hide: [] };
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
