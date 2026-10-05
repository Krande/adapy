// Provider options: values a provider asks for that change WHAT its requests fetch, set once per
// collection and sent with every request for it -- the tree, and geometry for its nodes.
//
// WHO DECLARES WHAT. A provider's spec names, in `asset_request_options.options`, which of its own
// `job_options` are offered here; their types, titles and descriptions are those declarations.
// Optionally `asset_request_options.choices` is a request (`{options, collection_option}`) whose
// job answers what the options can be set to for one collection: `option_choices` on its summary,
// option name -> `[{value, label?, description?}]`. Core names no option and reads no value.
//
// WHY PER COLLECTION, NOT PER REQUEST. A collection is ONE published tree per provider. A tree
// requested with a value and geometry requested without it would not describe the same thing --
// the geometry request could not even find nodes the tree has. So the value belongs to the
// collection, and every request for it sends the same one.
//
// THE DOCUMENT is one blob per collection, `assets/_options/<collection>.json`, beside the saved
// view and the tree sets, shared by everyone in the scope.
//
// Pure: runs under `node --test`.

export const PROVIDER_OPTIONS_SCHEMA = "ada.assets/provider-options@1";

export function providerOptionsKey(collection: string): string {
  return `assets/_options/${collection}.json`;
}

export type ProviderOptionValues = Readonly<Record<string, unknown>>;

export interface ProviderOptionsDoc {
  readonly schema: typeof PROVIDER_OPTIONS_SCHEMA;
  /** provider id -> option name -> value. */
  readonly providers: Readonly<Record<string, ProviderOptionValues>>;
  readonly updated_at?: string;
}

export const EMPTY_PROVIDER_OPTIONS: ProviderOptionsDoc = { schema: PROVIDER_OPTIONS_SCHEMA, providers: {} };

export class ProviderOptionsDocError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProviderOptionsDocError";
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/** Parse the stored document. Empty reads as nothing set; an UNKNOWN SCHEMA THROWS, because the
 *  values decide what every request fetches and a viewer must not write over what it cannot read. */
export function parseProviderOptionsDoc(raw: unknown): ProviderOptionsDoc {
  let value: unknown = raw;
  if (typeof raw === "string") {
    if (!raw.trim()) return EMPTY_PROVIDER_OPTIONS;
    try {
      value = JSON.parse(raw);
    } catch {
      throw new ProviderOptionsDocError("the provider options document is not JSON");
    }
  }
  if (value === null || value === undefined) return EMPTY_PROVIDER_OPTIONS;
  if (!isRecord(value)) throw new ProviderOptionsDocError("the provider options document is not a JSON object");
  if (value.schema !== PROVIDER_OPTIONS_SCHEMA) {
    throw new ProviderOptionsDocError(
      `the provider options document is ${JSON.stringify(value.schema)}; this viewer reads ` +
        `${PROVIDER_OPTIONS_SCHEMA} only, and will not write over a document it cannot fully read`,
    );
  }
  const providers: Record<string, ProviderOptionValues> = {};
  if (isRecord(value.providers)) {
    for (const [id, values] of Object.entries(value.providers)) {
      if (id && isRecord(values)) providers[id] = { ...values };
    }
  }
  return {
    schema: PROVIDER_OPTIONS_SCHEMA,
    providers,
    ...(typeof value.updated_at === "string" ? { updated_at: value.updated_at } : {}),
  };
}

export function serialiseProviderOptionsDoc(doc: ProviderOptionsDoc): string {
  return JSON.stringify(doc);
}

/** Is `v` a value worth sending? An empty list or string is the same as not set. */
function isSet(v: unknown): boolean {
  if (v === undefined || v === null) return false;
  if (typeof v === "string") return v.trim().length > 0;
  if (Array.isArray(v)) return v.length > 0;
  return true;
}

/** `doc` with one provider's values replaced -- unset ones dropped, and the provider removed when
 *  nothing is left. Every other provider's values are kept as stored. */
export function withProviderValues(doc: ProviderOptionsDoc, providerId: string, values: ProviderOptionValues, at: string): ProviderOptionsDoc {
  const kept = Object.fromEntries(Object.entries(values).filter(([, v]) => isSet(v)));
  const providers = { ...doc.providers };
  if (Object.keys(kept).length) providers[providerId] = kept;
  else delete providers[providerId];
  return { schema: PROVIDER_OPTIONS_SCHEMA, providers, updated_at: at };
}

/** What a request for `providerId` sends: its stored values for the options it declares now, set
 *  ones only. A value for an option the provider stopped declaring is not sent -- it would arrive
 *  as an option the provider does not know. */
export function requestValues(doc: ProviderOptionsDoc, providerId: string, declared: readonly string[]): Record<string, unknown> {
  const stored = doc.providers[providerId] ?? {};
  const out: Record<string, unknown> = {};
  for (const name of declared) if (isSet(stored[name])) out[name] = stored[name];
  return out;
}

/** One value an option can take, as the provider's choices job describes it. */
export interface OptionChoice {
  readonly value: string;
  readonly label: string;
  readonly description?: string;
}

/** `option_choices` off a choices job's summary: option name -> its choices. Anything malformed
 *  is dropped; a summary without it is no choices at all. */
export function parseOptionChoices(summary: unknown): Record<string, OptionChoice[]> {
  const out: Record<string, OptionChoice[]> = {};
  const raw = isRecord(summary) ? summary.option_choices : undefined;
  if (!isRecord(raw)) return out;
  for (const [name, list] of Object.entries(raw)) {
    if (!Array.isArray(list)) continue;
    const seen = new Set<string>();
    const choices: OptionChoice[] = [];
    for (const c of list) {
      const value = isRecord(c) && typeof c.value === "string" ? c.value : typeof c === "string" ? c : "";
      if (!value || seen.has(value)) continue;
      seen.add(value);
      const label = isRecord(c) && typeof c.label === "string" && c.label ? c.label : value;
      const description = isRecord(c) && typeof c.description === "string" && c.description ? c.description : undefined;
      choices.push({ value, label, ...(description ? { description } : {}) });
    }
    out[name] = choices;
  }
  return out;
}
