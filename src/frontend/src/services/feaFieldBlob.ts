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

import type {FeaFetcher} from "./fea/feaFetcher";
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

export function clearFieldBlobCache(): void {
    BLOB_CACHE.clear();
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
): {fetcher: FeaFetcher; cacheKey: string} {
    const cleanSrc = sourceKey.replace(/^\/+/, "");
    const prefix = `_derived/${cleanSrc}.fea/`;
    const cacheKey = `${scope}::${prefix}`;
    const fetcher: FeaFetcher = async (filename: string) => {
        const {viewerApi} = await import("./viewerApi");
        return viewerApi.getBlob(scope, `${prefix}${filename.replace(/^\/+/, "")}`);
    };
    return {fetcher, cacheKey};
}
