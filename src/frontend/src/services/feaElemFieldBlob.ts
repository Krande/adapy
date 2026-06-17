// AFEL (Adapy Field Element) blob parser — element-field counterpart
// to feaFieldBlob (AFBL). One blob per (logical field, element type).
//
// Format mirrors ada/fem/results/artefacts.py
// (_encode_elem_field_blob_header + write_element_field_blob_streaming):
//
//   bytes  content
//   0..3   magic = "AFEL"
//   4..7   uint32 little-endian, version (=1)
//   8..11  uint32 little-endian, json_header_len
//   12..   UTF-8 JSON header
//   ..1023 zero-pad to 1024 bytes total
//   1024+  step-major payload, n_steps × n_elements × n_ips × n_components × float32
//
// The header is O(1): name, elem_type, n_steps, n_elements, n_ips,
// n_components, dtype, stride_bytes. Per-step ``element_labels`` and
// ``ip_layout`` live in the manifest's per_type bucket, not the blob,
// so the binary stays compact even for very large element counts.

import {disableFeaRange, feaRangeSupported} from "./fea/feaFetcher";
import type {FeaFetcher, FeaRangeFetcher} from "./fea/feaFetcher";
import type {FeaManifestFieldPerType} from "./viewerApi";

const ELEM_FIELD_MAGIC = 0x4c454641; // "AFEL" little-endian
const ELEM_FIELD_HEADER_BYTES = 1024;
const ELEM_FIELD_VERSION = 1;

export interface FeaElemFieldBlobHeader {
    name: string;
    elem_type: string;
    n_steps: number;
    n_elements: number;
    n_ips: number;
    n_components: number;
    dtype: string;
    stride_bytes: number;
}

export interface ParsedFeaElemFieldBlob {
    header: FeaElemFieldBlobHeader;
    /** ``n_steps`` views into the underlying ArrayBuffer. Each view is
     *  laid out as ``(n_elements, n_ips, n_components)`` C-contiguous:
     *  index ``[e, ip, c]`` lives at offset
     *  ``e * (n_ips * n_components) + ip * n_components + c``.
     *  The reduction kernel walks this directly rather than reshaping. */
    steps: Float32Array[];
}

/** Parse an AFEL blob's bytes into a header + per-step views. */
export function parseElemFieldBlob(buf: ArrayBuffer): ParsedFeaElemFieldBlob {
    if (buf.byteLength < ELEM_FIELD_HEADER_BYTES) {
        throw new Error(
            `elem field blob too small: ${buf.byteLength} bytes, ` +
            `need ${ELEM_FIELD_HEADER_BYTES} for header`,
        );
    }
    const dv = new DataView(buf);
    const magic = dv.getUint32(0, true);
    if (magic !== ELEM_FIELD_MAGIC) {
        throw new Error(`elem field blob: bad magic 0x${magic.toString(16)}`);
    }
    const version = dv.getUint32(4, true);
    if (version !== ELEM_FIELD_VERSION) {
        throw new Error(
            `elem field blob: version ${version}, expected ${ELEM_FIELD_VERSION}`,
        );
    }
    const jsonLen = dv.getUint32(8, true);
    if (12 + jsonLen > ELEM_FIELD_HEADER_BYTES) {
        throw new Error(
            `elem field blob: header JSON ${jsonLen} bytes overflows fixed prefix`,
        );
    }
    const jsonBytes = new Uint8Array(buf, 12, jsonLen);
    const header = JSON.parse(new TextDecoder().decode(jsonBytes)) as FeaElemFieldBlobHeader;

    if (header.dtype !== "float32") {
        throw new Error(`elem field blob: unsupported dtype ${JSON.stringify(header.dtype)}`);
    }

    const expectedPayload = header.n_steps * header.stride_bytes;
    if (buf.byteLength < ELEM_FIELD_HEADER_BYTES + expectedPayload) {
        throw new Error(
            `elem field blob: payload ${buf.byteLength - ELEM_FIELD_HEADER_BYTES} bytes, ` +
            `expected ${expectedPayload} (n_steps=${header.n_steps}, stride=${header.stride_bytes})`,
        );
    }

    const stepLen = header.n_elements * header.n_ips * header.n_components;
    const steps: Float32Array[] = new Array(header.n_steps);
    for (let i = 0; i < header.n_steps; i++) {
        const offset = ELEM_FIELD_HEADER_BYTES + i * header.stride_bytes;
        steps[i] = new Float32Array(buf, offset, stepLen);
    }
    return {header, steps};
}

// Per-(scope, source, bucket-url) cache. Buckets are immutable per
// bake, so we never re-fetch unless the user picks a different field.
const ELEM_BLOB_CACHE = new Map<string, Promise<ParsedFeaElemFieldBlob>>();

// Per-(bucket, step) cache for the range-fetched single steps.
const ELEM_STEP_CACHE = new Map<string, Promise<Float32Array>>();

export function clearElemFieldBlobCache(): void {
    ELEM_BLOB_CACHE.clear();
    ELEM_STEP_CACHE.clear();
}

/** Fetch a single step's values for one AFEL element-type bucket by
 * HTTP-Range — the element-field counterpart of ``fetchFieldStep``. Same
 * always-safe fallbacks: whole-blob cache hit; Range disabled / unsupported
 * payload → plain whole-blob fetch; ranged request fails → disable Range +
 * whole-blob; server ignores Range (legacy gzip) → parse whole + slice. */
export async function fetchElemFieldStep(
    rangeFetcher: FeaRangeFetcher,
    fetcher: FeaFetcher,
    bucket: FeaManifestFieldPerType,
    stepIndex: number,
    cacheKey: string,
): Promise<Float32Array> {
    const blob = bucket.blob;
    const fullKey = `${cacheKey}::${blob.url}`;

    const wholeCached = ELEM_BLOB_CACHE.get(fullKey);
    if (wholeCached) return (await wholeCached).steps[stepIndex];

    const wholeFallback = async () => (await fetchElemFieldBlob(fetcher, bucket, cacheKey)).steps[stepIndex];

    if (!feaRangeSupported() || blob.dtype !== "float32" || blob.byte_order === "big") {
        return wholeFallback();
    }

    const stepKey = `${fullKey}::${stepIndex}`;
    const cached = ELEM_STEP_CACHE.get(stepKey);
    if (cached) return cached;

    const stride = blob.stride_bytes;
    const start = blob.header_bytes + stepIndex * stride;
    const end = start + stride - 1;
    const promise = (async () => {
        let res: {buf: ArrayBuffer; ranged: boolean};
        try {
            res = await rangeFetcher(blob.url, start, end);
        } catch (err) {
            // eslint-disable-next-line no-console
            console.warn(`[fea] range fetch failed for ${blob.url}; using whole-blob fallback`, err);
            disableFeaRange();
            return wholeFallback();
        }
        const {buf, ranged} = res;
        if (ranged) {
            if (buf.byteLength < stride) {
                throw new Error(
                    `elem field bucket ${bucket.elem_type} step ${stepIndex}: short range ` +
                    `(${buf.byteLength} < ${stride} bytes)`,
                );
            }
            return new Float32Array(buf, 0, stride / 4);
        }
        const parsed = parseElemFieldBlob(buf);
        ELEM_BLOB_CACHE.set(fullKey, Promise.resolve(parsed));
        return parsed.steps[stepIndex];
    })();
    ELEM_STEP_CACHE.set(stepKey, promise);
    try {
        return await promise;
    } catch (err) {
        ELEM_STEP_CACHE.delete(stepKey);
        throw err;
    }
}

/** Fetch + parse the AFEL blob for one (source, field, elem-type
 * bucket). Cached across calls — switching steps within a bucket
 * never re-fetches.
 *
 * `fetcher` resolves the manifest-relative filename to bytes;
 * `cacheKey` is an opaque per-bundle string the caller picks so
 * different bundles served from different roots don't collide. */
export async function fetchElemFieldBlob(
    fetcher: FeaFetcher,
    bucket: FeaManifestFieldPerType,
    cacheKey: string,
): Promise<ParsedFeaElemFieldBlob> {
    const fullCacheKey = `${cacheKey}::${bucket.blob.url}`;
    const cached = ELEM_BLOB_CACHE.get(fullCacheKey);
    if (cached) return cached;

    const promise = (async () => {
        const buf = await fetcher(bucket.blob.url);
        return parseElemFieldBlob(buf);
    })();
    ELEM_BLOB_CACHE.set(fullCacheKey, promise);
    try {
        return await promise;
    } catch (err) {
        ELEM_BLOB_CACHE.delete(fullCacheKey);
        throw err;
    }
}
