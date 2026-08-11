/**
 * Pure helpers for a topology instance's user-defined METADATA map — the open,
 * compiler-ignored key/value bag every Topo* entity (space / equipment /
 * opening / loft member) carries and round-trips through the procedural doc (and
 * thus the DB). Excel import folds every non-builtin column into this map; the
 * cellbuilder's Metadata editor reads + writes it here.
 *
 * Kept side-effect free and store-independent so the parse/format + map edits
 * are node-testable, mirroring `groups.ts`/`blueprints.ts`. The store persists a
 * whole replacement map through its existing withHistory actions
 * (`setCellParam(id, "METADATA", …)` for boxes, `setLoftMemberMetadata` for loft
 * members), so these helpers never touch the store — they only transform maps.
 *
 * Engine-agnostic: no metadata KEY is ever hardcoded here. Values are an opaque
 * string→(string|number|bool|json) space.
 */

export type MetaValue = string | number | boolean | Record<string, unknown> | unknown[];
export type MetaMap = Record<string, unknown>;

/** Coerce a raw METADATA field (unknown / absent / non-object) to an object
 * map, so a missing or malformed value edits as an empty map rather than
 * throwing. Arrays are NOT maps here (METADATA is a JSON object). */
export function asMetaObject(raw: unknown): MetaMap {
  return raw && typeof raw === "object" && !Array.isArray(raw)
    ? (raw as MetaMap)
    : {};
}

// Accepts an optionally-signed decimal / scientific-notation number and nothing
// else (no "1,2", "0x1", "Infinity", trailing junk). Paired with a Number()
// finiteness check so only unambiguous numbers convert.
const NUMERIC_RE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

/**
 * Parse a text-field input into a typed metadata value so an edited `"5"`
 * stores as the number `5` (not `"5"`) and `"true"` as the boolean — otherwise
 * the value stays a string. A `{...}` / `[...]` literal that parses as JSON is
 * kept as the parsed object/array; a malformed one stays a string. Minimal and
 * predictable: only these unambiguous forms convert, everything else is text
 * (leading/trailing whitespace is significant for strings, so the ORIGINAL
 * input — not the trimmed probe — is returned when it stays a string).
 */
export function parseMetadataValue(input: string): MetaValue {
  const t = input.trim();
  if (t === "") return input;
  if (t === "true") return true;
  if (t === "false") return false;
  if (NUMERIC_RE.test(t) && Number.isFinite(Number(t))) return Number(t);
  if (
    (t.startsWith("{") && t.endsWith("}")) ||
    (t.startsWith("[") && t.endsWith("]"))
  ) {
    try {
      return JSON.parse(t) as MetaValue;
    } catch {
      /* not valid JSON — fall through and keep it as a string */
    }
  }
  return input;
}

/** Render a stored metadata value for the text field: strings verbatim, every
 * other JSON type stringified so it displays AND re-parses through
 * {@link parseMetadataValue}. Inverse of parse for the round-trippable forms. */
export function formatMetadataValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** An empty map serialises as no METADATA key at all (never `METADATA={}`), so
 * callers pass this to the store's onCommit to drop the key when the last field
 * is removed. */
export function metaOrNull(meta: MetaMap): MetaMap | null {
  return Object.keys(meta).length ? meta : null;
}

/** Set/replace one key to the PARSED form of `input`. Always returns a new map
 * (an edit is always a commit, even to the same displayed text). */
export function setMetadataValue(meta: MetaMap, key: string, input: string): MetaMap {
  return { ...meta, [key]: parseMetadataValue(input) };
}

/** Rename a key, preserving insertion order (so the row doesn't jump). No-op —
 * returns the SAME reference — on a blank / unchanged / already-present target
 * or a missing source, so the caller can skip committing an identity edit. */
export function renameMetadataKey(meta: MetaMap, oldKey: string, newKey: string): MetaMap {
  const nk = newKey.trim();
  if (!nk || nk === oldKey || nk in meta || !(oldKey in meta)) return meta;
  const out: MetaMap = {};
  for (const [k, v] of Object.entries(meta)) out[k === oldKey ? nk : k] = v;
  return out;
}

/** Remove a key. Returns the SAME reference when the key is absent (no commit). */
export function removeMetadataKey(meta: MetaMap, key: string): MetaMap {
  if (!(key in meta)) return meta;
  const out = { ...meta };
  delete out[key];
  return out;
}

/** Append a fresh empty-string field under a unique auto-name (`key`, `key1`,
 * …) so the user can name + fill it. Always returns a new map. */
export function addMetadataKey(meta: MetaMap): MetaMap {
  let k = "key";
  let i = 1;
  while (k in meta) k = `key${i++}`;
  return { ...meta, [k]: "" };
}
