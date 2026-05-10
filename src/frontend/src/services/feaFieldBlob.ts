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

// Per-(scope, source, field) cache: blobs are immutable per-bake so
// we never re-fetch unless the user picks a different field. Keyed
// off the stable storage URL.
const BLOB_CACHE = new Map<string, Promise<ParsedFeaFieldBlob>>();

export function clearFieldBlobCache(): void {
    BLOB_CACHE.clear();
}

/** Fetch + parse the field blob for one (source, field). Cached
 * across calls — switching steps within a field never re-fetches. */
export async function fetchFieldBlob(
    scope: ScopeUrl,
    sourceKey: string,
    field: FeaManifestField,
): Promise<ParsedFeaFieldBlob> {
    const cleanSrc = sourceKey.replace(/^\/+/, "");
    const blobKey = `_derived/${cleanSrc}.fea/${field.blob.url}`;
    const cacheKey = `${scope}::${blobKey}`;
    const cached = BLOB_CACHE.get(cacheKey);
    if (cached) return cached;

    const promise = (async () => {
        // Lazy import: pulling viewerApi at module-top transitively
        // loads services/auth/oidc which touches sessionStorage and
        // breaks Node-side tests of the pure parser. Defer it to the
        // call site, which only fires in the browser.
        const {viewerApi} = await import("./viewerApi");
        const buf = await viewerApi.getBlob(scope, blobKey);
        return parseFieldBlob(buf);
    })();
    // Race-safe: the promise lands in the cache before the await
    // resolves so concurrent callers share the same fetch.
    BLOB_CACHE.set(cacheKey, promise);
    try {
        return await promise;
    } catch (err) {
        BLOB_CACHE.delete(cacheKey);
        throw err;
    }
}
