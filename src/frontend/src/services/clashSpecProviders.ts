// Which joint-detailing spec PROVIDERS a scope uses, and in what order of preference.
//
// A PROVIDER IS A POOL. Core never learns which package registered a connection spec -- only the
// capability it is served by (`ApplicableSpec.capability`), with `null` meaning one of core's own
// built-ins. So the provider of a spec is its capability, and `"builtin"` stands for the null one.
//
// THE SHAPE, stored as JSON text under one public setting, keyed per scope like
// `public.assets.scope_collections`:
//
//   public.clash.spec_providers = { "<scope url>": ["mesh-pool", "builtin"] }
//
// ORDER IS PREFERENCE. When a joint has applicable specs from several providers, the first enabled
// provider in the list details it; within one provider the spec's own priority decides, as before.
// A provider left out of the list is not offered in that scope at all.
//
// ADMIN DEFAULT, RUN OVERRIDE. The setting is what a scope starts with; the Clashes panel can
// override it for the session (`clashCheckStore.specProvidersOverride`) without writing it back.
//
// MISSING IS NOT EMPTY, the same rule as the collections grant: a scope nobody configured is
// UNRESTRICTED (every provider, ranked by spec priority alone -- what the panel always did), and a
// stored `[]` enables nothing. `null` and `[]` are different types of answer and are kept apart.
//
// Pure, so it runs under plain node, for the reason `assetScopeCollections.ts` gives.

/** The public setting holding each scope's ordered spec-provider preference. */
export const CLASH_SPEC_PROVIDERS_KEY = "public.clash.spec_providers";

/** The provider id that stands for core's own built-in specs (`capability: null`). */
export const BUILTIN_SPEC_PROVIDER = "builtin";

/** An ordered list of enabled providers, or `null` for unrestricted. */
export type SpecProviderPreference = readonly string[] | null;

/** scope url -> ordered enabled providers, as stored. */
export type SpecProvidersMap = Readonly<Record<string, readonly string[]>>;

/** The provider a spec belongs to. */
export function specProviderOf(spec: { readonly capability: string | null }): string {
  return spec.capability ?? BUILTIN_SPEC_PROVIDER;
}

/** Parse the stored setting. Missing or malformed reads as an empty map -- every scope
 *  unrestricted -- rather than throwing: unconfigured is the normal case. A scope entry that is
 *  not a list is dropped whole, leaving that scope unrestricted. */
export function parseSpecProviders(raw: unknown): SpecProvidersMap {
  if (!raw) return {};
  let parsed: unknown = raw;
  if (typeof raw === "string") {
    try {
      parsed = JSON.parse(raw);
    } catch {
      return {};
    }
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
  const out: Record<string, string[]> = {};
  for (const [scope, list] of Object.entries(parsed as Record<string, unknown>)) {
    if (!Array.isArray(list)) continue;
    out[scope] = uniq(list.filter((p): p is string => typeof p === "string" && p.trim().length > 0));
  }
  return out;
}

/** The preference for `scope`: `null` when unconfigured, otherwise the stored list. */
export function preferenceFor(map: SpecProvidersMap, scope: string): SpecProviderPreference {
  const list = map[scope];
  return list === undefined ? null : list;
}

/** A copy of `map` with `scope` set to `pref`. `null` removes the entry (unrestricted again). */
export function withPreference(map: SpecProvidersMap, scope: string, pref: SpecProviderPreference): SpecProvidersMap {
  const out: Record<string, readonly string[]> = { ...map };
  if (pref === null) delete out[scope];
  else out[scope] = uniq(pref);
  return out;
}

export function serialiseSpecProviders(map: SpecProvidersMap): string {
  return JSON.stringify(map);
}

/** The applicable specs a preference allows, best first: by the provider's rank in the
 *  preference, then by spec priority, then by name so the answer is stable. Unrestricted keeps
 *  every spec and ranks by priority alone. */
export function rankSpecs<T extends { readonly spec: string; readonly capability: string | null; readonly priority: number }>(
  applicable: readonly T[],
  pref: SpecProviderPreference,
): T[] {
  const rank = (s: T): number => (pref === null ? 0 : pref.indexOf(specProviderOf(s)));
  return applicable
    .filter((s) => rank(s) >= 0)
    .sort((a, b) => rank(a) - rank(b) || b.priority - a.priority || a.spec.localeCompare(b.spec));
}

/** One row of the provider chooser: every provider some spec could come from, plus any the
 *  preference names that nothing advertises right now (an offline pool is not a revocation -- the
 *  same rule the collections tab keeps). Enabled ones first, in preference order. */
export interface SpecProviderChoice {
  readonly provider: string;
  readonly enabled: boolean;
  readonly advertised: boolean;
}

export function specProviderChoices(
  advertised: readonly string[],
  pref: SpecProviderPreference,
): SpecProviderChoice[] {
  const known = uniq([BUILTIN_SPEC_PROVIDER, ...advertised]);
  if (pref === null) return known.map((provider) => ({ provider, enabled: true, advertised: true }));
  const out: SpecProviderChoice[] = pref.map((provider) => ({
    provider,
    enabled: true,
    advertised: known.includes(provider),
  }));
  for (const provider of known) {
    if (!pref.includes(provider)) out.push({ provider, enabled: false, advertised: true });
  }
  return out;
}

/** `pref` with `provider` switched on (appended last) or off. Unrestricted becomes the full list
 *  of `advertised` providers first, so unticking one of "all" changes only that one. */
export function toggleSpecProvider(
  pref: SpecProviderPreference,
  advertised: readonly string[],
  provider: string,
  on: boolean,
): readonly string[] {
  const base = pref === null ? uniq([BUILTIN_SPEC_PROVIDER, ...advertised]) : [...pref];
  const rest = base.filter((p) => p !== provider);
  return on ? [...rest, provider] : rest;
}

/** `pref` with `provider` moved one place towards the front (`-1`) or back (`+1`). */
export function moveSpecProvider(pref: readonly string[], provider: string, delta: -1 | 1): readonly string[] {
  const i = pref.indexOf(provider);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= pref.length) return pref;
  const out = [...pref];
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

/** How a provider reads in the UI. */
export function specProviderLabel(provider: string): string {
  return provider === BUILTIN_SPEC_PROVIDER ? "adapy (built-in)" : provider;
}

function uniq(list: readonly string[]): string[] {
  return [...new Set(list)];
}
