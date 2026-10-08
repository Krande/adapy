// What the UI SHOWS for an asset provider -- never what it keys on.
//
// A provider id is an identity: it is in manifests, storage keys, saved sets and URLs, and it
// never changes. Its display name is a separate, per-scope answer the server resolves
// (`GET /scopes/{scope}/assets/providers` -> `provider_labels`):
//
//   1. an admin's alias for the provider in that scope (`public.assets.provider_labels`,
//      edited in Admin > Providers),
//   2. the provider's own label (`asset_provider_label` on its plugin spec, or its registration),
//   3. nothing -- and then the id is shown.
//
// So everything here keeps comparing, storing and sending ids, and calls `providerDisplayName`
// only where text is rendered, with the id kept in the tooltip (`providerIdTitle`).
//
// Pure and import-free, so it is testable under plain node.

/** provider id -> display name, as resolved for one scope. A provider with none is absent. */
export type ProviderLabels = Readonly<Record<string, string>>;

/** The public setting holding the admin aliases: scope -> provider id -> display name. */
export const PROVIDER_LABELS_KEY = "public.assets.provider_labels";

/** Must match the server's `MAX_PROVIDER_LABEL_LENGTH`; the server refuses longer. */
export const MAX_PROVIDER_LABEL_LENGTH = 64;

/** scope -> provider id -> alias, as stored. */
export type ProviderAliasesMap = Readonly<Record<string, Readonly<Record<string, string>>>>;

/** A display name as the server would store it: trimmed, inner whitespace collapsed. `null` when
 *  that leaves nothing (meaning "no alias -- fall back"). Length is checked by `labelProblem`. */
export function cleanProviderLabel(value: string): string | null {
  const label = value.split(/\s+/).filter(Boolean).join(" ");
  return label ? label : null;
}

/** Why `value` cannot be saved as a display name, or `null` when it can (empty is fine: it clears). */
export function labelProblem(value: string): string | null {
  const label = cleanProviderLabel(value);
  if (label && label.length > MAX_PROVIDER_LABEL_LENGTH) {
    return `At most ${MAX_PROVIDER_LABEL_LENGTH} characters (this is ${label.length}).`;
  }
  return null;
}

/** Parse a `provider_labels` object: string values only, trimmed, empty dropped. */
export function parseProviderLabels(raw: unknown): ProviderLabels {
  if (!isRecord(raw)) return {};
  const out: Record<string, string> = {};
  for (const [id, label] of Object.entries(raw)) {
    if (!id || typeof label !== "string") continue;
    const clean = cleanProviderLabel(label);
    if (clean) out[id] = clean;
  }
  return out;
}

/** The name to show for `id`: the first source naming it, else the id itself. Sources are
 *  consulted in the order given -- pass the server's resolved labels first. */
export function providerDisplayName(id: string, ...sources: readonly (ProviderLabels | null | undefined)[]): string {
  for (const source of sources) {
    const label = source?.[id];
    if (label) return label;
  }
  return id;
}

/** The tooltip that keeps the raw id reachable wherever a display name replaces it. */
export function providerIdTitle(id: string): string {
  return `Provider id: ${id}`;
}

/** Parse the stored alias setting (JSON text, or an object). Missing or malformed is empty. */
export function parseProviderAliases(raw: unknown): ProviderAliasesMap {
  let parsed: unknown = raw;
  if (typeof raw === "string") {
    try {
      parsed = JSON.parse(raw);
    } catch {
      return {};
    }
  }
  if (!isRecord(parsed)) return {};
  const out: Record<string, Record<string, string>> = {};
  for (const [scope, row] of Object.entries(parsed)) {
    const labels = parseProviderLabels(row);
    if (Object.keys(labels).length) out[scope] = { ...labels };
  }
  return out;
}

/** A copy of `map` with `(scope, provider)` set to `label`; an empty label removes the alias, and a
 *  scope left with none is removed with it. */
export function withProviderAlias(
  map: ProviderAliasesMap,
  scope: string,
  provider: string,
  label: string,
): ProviderAliasesMap {
  const out: Record<string, Record<string, string>> = {};
  for (const [s, row] of Object.entries(map)) out[s] = { ...row };
  const row = out[scope] ?? {};
  const clean = cleanProviderLabel(label);
  if (clean) row[provider] = clean;
  else delete row[provider];
  if (Object.keys(row).length) out[scope] = row;
  else delete out[scope];
  return out;
}

/** The stored form: JSON text, because the settings route stores a string. */
export function serialiseProviderAliases(map: ProviderAliasesMap): string {
  return JSON.stringify(map);
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}
