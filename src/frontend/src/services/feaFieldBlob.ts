// AFBL (Adapy Field BLob) parser — mirror of the Python writer in
// ada/fem/results/artefacts.py. The format is:
//
//   bytes  content
//   0..3   magic = "AFBL"
//   4..7   uint32 little-endian, version (=1)
//   8..11  uint32 little-endian, json_header_len
//   12..   UTF-8 JSON header
//   ..1023 zero-pad to 1024 bytes total
//   1024+  step-major payload, n_steps × n_points × n_components × float32
//
// The header is O(1) — name, n_steps, n_points, n_components, dtype,
// stride_bytes — so a 1024-byte initial fetch always covers it.
// Per-step labels and time/freq values live in the manifest, not
// the blob.

import {disableFeaRange, feaRangeSupported} from "./fea/feaFetcher";
import type {FeaFetcher, FeaRangeFetcher} from "./fea/feaFetcher";
import type {FeaManifestField, ScopeUrl} from "./viewerApi";

const BLOB_MAGIC = 0x4c424641; // "AFBL" little-endian
const BLOB_HEADER_BYTES = 1024;
const BLOB_VERSION = 1;

export interface FeaFieldBlobHeader {
    name: string;
    n_steps: number;
    n_points: number;
    n_components: number;
    dtype: string;
    stride_bytes: number;
}

export interface ParsedFeaFieldBlob {
    header: FeaFieldBlobHeader;
    /** ``n_steps`` views into the underlying ArrayBuffer, each of
     * length ``n_points × n_components``. Float32-typed. Sharing the
     * buffer means switching steps is a slice, not a fetch. */
    steps: Float32Array[];
}

/** Parse an AFBL blob's bytes into a header + per-step views. */
export function parseFieldBlob(buf: ArrayBuffer): ParsedFeaFieldBlob {
    if (buf.byteLength < BLOB_HEADER_BYTES) {
        throw new Error(
            `field blob too small: ${buf.byteLength} bytes, need ${BLOB_HEADER_BYTES} for header`,
        );
    }
    const dv = new DataView(buf);
    const magic = dv.getUint32(0, true);
    if (magic !== BLOB_MAGIC) {
        throw new Error(`field blob: bad magic 0x${magic.toString(16)}`);
    }
    const version = dv.getUint32(4, true);
    if (version !== BLOB_VERSION) {
        throw new Error(`field blob: version ${version}, expected ${BLOB_VERSION}`);
    }
    const jsonLen = dv.getUint32(8, true);
    if (12 + jsonLen > BLOB_HEADER_BYTES) {
        throw new Error(`field blob: header JSON ${jsonLen} bytes overflows fixed prefix`);
    }
    const jsonBytes = new Uint8Array(buf, 12, jsonLen);
    const header = JSON.parse(new TextDecoder().decode(jsonBytes)) as FeaFieldBlobHeader;

    if (header.dtype !== "float32") {
        throw new Error(`field blob: unsupported dtype ${JSON.stringify(header.dtype)}`);
    }

    const expectedPayload = header.n_steps * header.stride_bytes;
    if (buf.byteLength < BLOB_HEADER_BYTES + expectedPayload) {
        throw new Error(
            `field blob: payload ${buf.byteLength - BLOB_HEADER_BYTES} bytes, ` +
            `expected ${expectedPayload} (n_steps=${header.n_steps}, stride=${header.stride_bytes})`,
        );
    }

    const stepLen = header.n_points * header.n_components;
    const steps: Float32Array[] = new Array(header.n_steps);
    for (let i = 0; i < header.n_steps; i++) {
        const offset = BLOB_HEADER_BYTES + i * header.stride_bytes;
        steps[i] = new Float32Array(buf, offset, stepLen);
    }
    return {header, steps};
}

// Per-bundle-URL cache: blobs are immutable per-bake so we never
// re-fetch unless the user navigates to a different bundle / field.
// Keyed off the storage URL the fetcher resolves to — both the
// standalone viewer's `_derived/<src>.fea/<filename>` keys and
// paradoc's `/api/docs/<id>/3d/<key>/fea/<filename>` URLs are stable.
const BLOB_CACHE = new Map<string, Promise<ParsedFeaFieldBlob>>();

// Per-(bundle, field, step) cache for the range-fetched single steps —
// the fast path. Keyed off the same stable url as BLOB_CACHE plus the
// step index, so dragging the step slider re-fetches each step at most
// once and switching back is free.
const STEP_CACHE = new Map<string, Promise<Float32Array>>();

export function clearFieldBlobCache(): void {
    BLOB_CACHE.clear();
    STEP_CACHE.clear();
}

/** Fetch a single step's values for a nodal field by HTTP-Range, pulling
 * only that step's stride instead of the whole multi-step blob — the fix
 * for many-step decks (a 200-mode eigen result would otherwise download
 * every mode's displacement just to show one).
 *
 * Always safe — never worse than the old whole-blob path:
 *   - whole blob already cached (a prior full fetch) → slice it;
 *   - Range disabled this session (a prior failure) or non-float32/big-
 *     endian → plain whole-blob `fetcher` (no Range header) + slice;
 *   - the ranged request *fails* (network/proxy/"Failed to fetch") →
 *     disable Range session-wide and fall back to the whole-blob fetcher;
 *   - the server *ignores* Range (legacy gzip blob → 200 whole) → parse
 *     the whole blob, cache it, slice.
 * Only new identity-stored blobs over a Range-capable transport get the
 * bandwidth win; everything else still works. */
export async function fetchFieldStep(
    rangeFetcher: FeaRangeFetcher,
    fetcher: FeaFetcher,
    field: FeaManifestField,
    stepIndex: number,
    cacheKey: string,
): Promise<Float32Array> {
    if (!field.blob) {
        throw new Error(
            `fetchFieldStep: field ${field.name_canonical} has no blob ` +
            `(category=${field.category}, support=${field.support}); ` +
            `element fields use the AFEL loader.`,
        );
    }
    const blob = field.blob;
    const fullKey = `${cacheKey}::${blob.url}`;

    // Already have the whole blob (a prior full fetch / fallback)? Slice it.
    const wholeCached = BLOB_CACHE.get(fullKey);
    if (wholeCached) return (await wholeCached).steps[stepIndex];

    const wholeFallback = async () => (await fetchFieldBlob(fetcher, field, cacheKey)).steps[stepIndex];

    // Range unavailable (disabled after an earlier failure) or a payload
    // the zero-copy view can't handle → plain whole-blob fetch.
    if (!feaRangeSupported() || blob.dtype !== "float32" || blob.byte_order === "big") {
        return wholeFallback();
    }

    const stepKey = `${fullKey}::${stepIndex}`;
    const cached = STEP_CACHE.get(stepKey);
    if (cached) return cached;

    const stride = blob.stride_bytes;
    const start = blob.header_bytes + stepIndex * stride;
    const end = start + stride - 1;
    const promise = (async () => {
        let res: {buf: ArrayBuffer; ranged: boolean};
        try {
            res = await rangeFetcher(blob.url, start, end);
        } catch (err) {
            // Network/proxy rejected the Range request ("Failed to fetch").
            // Disable Range for the session and fall back to whole-blob so
            // the viewer still loads (just without the per-step win).
            // eslint-disable-next-line no-console
            console.warn(`[fea] range fetch failed for ${blob.url}; using whole-blob fallback`, err);
            disableFeaRange();
            return wholeFallback();
        }
        const {buf, ranged} = res;
        if (ranged) {
            // The 206 body is exactly this step's stride — a 4-aligned,
            // offset-0 buffer, so a direct Float32Array view is valid.
            if (buf.byteLength < stride) {
                throw new Error(
                    `field ${field.name_canonical} step ${stepIndex}: short range ` +
                    `(${buf.byteLength} < ${stride} bytes)`,
                );
            }
            return new Float32Array(buf, 0, stride / 4);
        }
        // Server ignored Range (legacy gzipped blob): we got the whole
        // object. Parse + cache it so other steps slice for free.
        const parsed = parseFieldBlob(buf);
        BLOB_CACHE.set(fullKey, Promise.resolve(parsed));
        return parsed.steps[stepIndex];
    })();
    STEP_CACHE.set(stepKey, promise);
    try {
        return await promise;
    } catch (err) {
        STEP_CACHE.delete(stepKey);
        throw err;
    }
}

/** Fetch + parse the field blob for one (source, field). Cached
 * across calls — switching steps within a field never re-fetches.
 *
 * `fetcher` resolves a manifest-relative filename to bytes. Standalone
 * viewer wraps `viewerApi.getBlob` with the bake-job storage prefix
 * (`makeViewerApiFetcher`); paradoc wraps `authedFetch` against
 * paradoc-serve's `/api/docs/.../fea/` endpoint. `cacheKey` is an
 * opaque string the caller chooses so multiple bundles served from
 * different roots don't collide in the cache. */
export async function fetchFieldBlob(
    fetcher: FeaFetcher,
    field: FeaManifestField,
    cacheKey: string,
): Promise<ParsedFeaFieldBlob> {
    if (!field.blob) {
        // Element fields use per_type instead of a top-level blob and
        // have their own loader (Phase 4B). Calling this helper on an
        // element field is a bug — surface it explicitly rather than
        // constructing an undefined URL.
        throw new Error(
            `fetchFieldBlob: field ${field.name_canonical} has no blob ` +
            `(category=${field.category}, support=${field.support}); ` +
            `element fields use the AFEL loader, not the nodal AFBL one.`,
        );
    }
    const fullCacheKey = `${cacheKey}::${field.blob.url}`;
    const cached = BLOB_CACHE.get(fullCacheKey);
    if (cached) return cached;

    const promise = (async () => {
        const buf = await fetcher(field.blob!.url);
        return parseFieldBlob(buf);
    })();
    // Race-safe: the promise lands in the cache before the await
    // resolves so concurrent callers share the same fetch.
    BLOB_CACHE.set(fullCacheKey, promise);
    try {
        return await promise;
    } catch (err) {
        BLOB_CACHE.delete(fullCacheKey);
        throw err;
    }
}

/** Construct an `FeaFetcher` that wraps `viewerApi.getBlob` with the
 * standalone-viewer's `_derived/<src>.fea/` prefix. Returned alongside
 * a stable cache key the caller passes to `fetch*` helpers.
 *
 * Lazy-imports `viewerApi` because that module touches `sessionStorage`
 * at module-top via the auth chain — the pure parsers (and Node-side
 * tests of them) must not pay that import cost. */
export function makeViewerApiFetcher(
    scope: ScopeUrl,
    sourceKey: string,
): {fetcher: FeaFetcher; rangeFetcher: FeaRangeFetcher; cacheKey: string} {
    const cleanSrc = sourceKey.replace(/^\/+/, "");
    const prefix = `_derived/${cleanSrc}.fea/`;
    const cacheKey = `${scope}::${prefix}`;
    const fetcher: FeaFetcher = async (filename: string) => {
        const {viewerApi} = await import("./viewerApi");
        return viewerApi.getBlob(scope, `${prefix}${filename.replace(/^\/+/, "")}`);
    };
    const rangeFetcher: FeaRangeFetcher = async (filename, start, end) => {
        const {viewerApi} = await import("./viewerApi");
        return viewerApi.getBlobRange(scope, `${prefix}${filename.replace(/^\/+/, "")}`, start, end);
    };
    return {fetcher, rangeFetcher, cacheKey};
}
