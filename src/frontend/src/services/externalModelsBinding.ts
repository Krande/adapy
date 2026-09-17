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
  /** Glob patterns for models this scope should not see. Empty means show all.
   *  See {@link isHidden}. */
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

/** Does `text` match a glob with `*` and `?`, case-insensitively?
 *
 *  A GLOB RATHER THAN A REGEX, because these are written by an admin into a
 *  small box and `*Temp*` is what someone means. Everything else is escaped, so
 *  a pattern containing `.` or `(` matches those characters rather than
 *  quietly meaning something else -- a model named `AP400(511)-STRU` is an
 *  ordinary thing to want to hide.
 *
 *  A pattern with no wildcard at all is treated as a SUBSTRING, because that is
 *  what typing `Temp` into a filter box means to everyone who is not thinking
 *  about globs. */
export function matchesGlob(text: string, pattern: string): boolean {
  const p = pattern.trim();
  if (!p) return false;
  const hay = text.toLowerCase();
  const needle = p.toLowerCase();
  if (!needle.includes("*") && !needle.includes("?")) return hay.includes(needle);
  const escaped = needle.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  const rx = new RegExp(`^${escaped.replace(/\*/g, ".*").replace(/\?/g, ".")}$`);
  return rx.test(hay);
}

/** Should this model be hidden from the scope it was listed for?
 *
 *  Matched against the id, the name AND the description, because they carry
 *  different halves of what an admin is looking at: web3d names a model for its
 *  SITE and puts the RVM export in the description, so "hide the temporary
 *  steel exports" is a pattern over the description while "hide the VAT sites"
 *  is one over the name. Requiring the admin to know which would make the box
 *  fail silently half the time. */
export function isHidden(
  model: { id?: string; name?: string; description?: string | null },
  patterns: readonly string[],
): boolean {
  if (!patterns.length) return false;
  const fields = [model.id, model.name, model.description].filter(
    (f): f is string => typeof f === "string" && f.length > 0,
  );
  return patterns.some((p) => fields.some((f) => matchesGlob(f, p)));
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
