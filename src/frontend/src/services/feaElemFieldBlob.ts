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

import type {FeaManifestFieldPerType, ScopeUrl} from "./viewerApi";

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

export function clearElemFieldBlobCache(): void {
    ELEM_BLOB_CACHE.clear();
}

/** Fetch + parse the AFEL blob for one (source, field, elem-type
 * bucket). Cached across calls — switching steps within a bucket
 * never re-fetches. */
export async function fetchElemFieldBlob(
    scope: ScopeUrl,
    sourceKey: string,
    bucket: FeaManifestFieldPerType,
): Promise<ParsedFeaElemFieldBlob> {
    const cleanSrc = sourceKey.replace(/^\/+/, "");
    const blobKey = `_derived/${cleanSrc}.fea/${bucket.blob.url}`;
    const cacheKey = `${scope}::${blobKey}`;
    const cached = ELEM_BLOB_CACHE.get(cacheKey);
    if (cached) return cached;

    const promise = (async () => {
        // Lazy import: viewerApi pulls auth/oidc which touches
        // sessionStorage at module-top and breaks Node-side tests of
        // the pure parser. Defer to the browser call site.
        const {viewerApi} = await import("./viewerApi");
        const buf = await viewerApi.getBlob(scope, blobKey);
        return parseElemFieldBlob(buf);
    })();
    ELEM_BLOB_CACHE.set(cacheKey, promise);
    try {
        return await promise;
    } catch (err) {
        ELEM_BLOB_CACHE.delete(cacheKey);
        throw err;
    }
}
