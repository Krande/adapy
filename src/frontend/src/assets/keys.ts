/**
 * The asset key grammar, mirrored from `ada/assets/keys.py`.
 *
 * Two implementations of one grammar is a liability, so this file exists only because the browser
 * has to compose and recognise keys without a round trip. The Python side is authoritative; the
 * shared test vectors in `__tests__/assets/keys.test.ts` are what keep the two honest.
 *
 * `assets/<collection>/<subject>/<revision>/<file>` -- five segments, always. Arity is checked
 * FIRST, which is what keeps a four-segment `assets/_staging/<id>/<file>` key from parsing as an
 * asset whatever the staging id looks like.
 */

export const ASSET_PREFIX = "assets";
export const ASSET_KEY_SEGMENTS = 5;
export const STAGING_SEGMENT = "_staging";

/**
 * Every segment. URL-path-safe on purpose: a subject rides in a route path unescaped. `$` is
 * admitted so a 22-character IFC GlobalId is a subject VERBATIM rather than hex-expanded; a
 * leading `_` is reserved for core so `_staging` stays collision-free without reserved words.
 * The first character is narrower than the rest: no `.` (so `.`/`..` cannot appear) and no `-`
 * (so a segment cannot read as a CLI flag when a key is pasted into one).
 */
const SEGMENT_RE = /^[A-Za-z0-9][A-Za-z0-9._$-]{0,127}$/;

/** Compact UTC instant, so LEXICAL order is CHRONOLOGICAL order -- what "latest" leans on. */
const REVISION_RE = /^\d{8}T\d{6}Z$/;

export interface AssetKey {
  collection: string;
  subject: string;
  revision: string;
  filename: string;
}

export class AssetKeyError extends Error {}

export function isValidSegment(value: string): boolean {
  return SEGMENT_RE.test(value);
}

export function isValidRevision(value: string): boolean {
  return REVISION_RE.test(value);
}

function checkSegment(value: string, what: string): string {
  if (typeof value !== "string" || !SEGMENT_RE.test(value)) {
    throw new AssetKeyError(
      `invalid ${what} ${JSON.stringify(value)}: must match ${SEGMENT_RE.source} ` +
        `(start with a letter or digit; then letters, digits, '.', '_', '$' or '-'; max 128 chars; ` +
        `no '/', no leading '_' -- that is reserved for core)`,
    );
  }
  return value;
}

function checkRevision(value: string): string {
  if (typeof value !== "string" || !REVISION_RE.test(value)) {
    throw new AssetKeyError(
      `invalid revision ${JSON.stringify(value)}: must be a compact UTC instant like ` +
        `'20260921T143001Z' -- lexical order has to equal chronological order, so a local-time or ` +
        `offset-bearing stamp is refused`,
    );
  }
  return value;
}

/** True when this key holds the COLLECTION's own artefacts rather than a node's. */
export function isCollectionLevel(key: AssetKey): boolean {
  return key.subject === key.collection;
}

export function parseAssetKey(key: string): AssetKey {
  if (typeof key !== "string" || key.length === 0) {
    throw new AssetKeyError(`invalid asset key ${JSON.stringify(key)}: expected a non-empty string`);
  }
  const parts = key.split("/");
  if (parts.length !== ASSET_KEY_SEGMENTS) {
    throw new AssetKeyError(
      `invalid asset key ${JSON.stringify(key)}: expected exactly ${ASSET_KEY_SEGMENTS} segments ` +
        `('${ASSET_PREFIX}/<collection>/<subject>/<revision>/<file>'), got ${parts.length}. ` +
        `A ${STAGING_SEGMENT} key has 4 segments and is never an asset key.`,
    );
  }
  const [prefix, collection, subject, revision, filename] = parts;
  if (prefix !== ASSET_PREFIX) {
    throw new AssetKeyError(
      `invalid asset key ${JSON.stringify(key)}: must start with '${ASSET_PREFIX}/', got ${JSON.stringify(prefix)}`,
    );
  }
  return {
    collection: checkSegment(collection, "collection"),
    subject: checkSegment(subject, "subject"),
    revision: checkRevision(revision),
    filename: checkSegment(filename, "filename"),
  };
}

export function assetKey(collection: string, subject: string, revision: string, filename: string): string {
  return [
    ASSET_PREFIX,
    checkSegment(collection, "collection"),
    checkSegment(subject, "subject"),
    checkRevision(revision),
    checkSegment(filename, "filename"),
  ].join("/");
}

export function stagingPrefix(stagingId: string): string {
  if (typeof stagingId !== "string" || stagingId.length === 0 || stagingId.includes("/")) {
    throw new AssetKeyError(
      `invalid staging id ${JSON.stringify(stagingId)}: expected a non-empty string with no '/'`,
    );
  }
  return `${ASSET_PREFIX}/${STAGING_SEGMENT}/${stagingId}/`;
}

/**
 * Normalise an instant to the compact revision form.
 *
 * A value with no timezone is REFUSED rather than assumed UTC: guessing would place a revision at
 * the wrong point in an ordering that cannot be repaired once neighbours exist.
 */
export function revisionFromInstant(instant: string | Date): string {
  if (typeof instant === "string" && !/(Z|[+-]\d{2}:?\d{2})$/.test(instant.trim())) {
    throw new AssetKeyError(
      `invalid instant ${JSON.stringify(instant)}: no timezone. Pass an offset-bearing string -- a ` +
        `naive instant would be silently placed in the wrong chronological order.`,
    );
  }
  const d = instant instanceof Date ? instant : new Date(instant);
  if (Number.isNaN(d.getTime())) {
    throw new AssetKeyError(`invalid instant ${JSON.stringify(instant)}: not a parseable instant`);
  }
  return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}
