// Element-field (AFEL) blobs a plugin ships in its own sidecar, read through core's reader.
//
// A result plugin that writes its per-element values in core's AFEL layout -- one float32 per
// element per step behind the 1 KiB JSON header (feaElemFieldBlob.ts) -- reads them here with
// the same range-or-whole-blob fallbacks, header checks and caches as core's own fields, through
// the `SidecarFetcher` its loader was handed.

import type {SidecarFetcher} from "@/plugins/registry";
import type {FeaManifestFieldPerType} from "@/services/api/fea";
import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";

/** Where a blob is and how it is laid out (the AFEL header's `stride_bytes`; the header is 1024 bytes). */
export interface SidecarElementFieldBlob {
    url: string;
    stride_bytes: number;
    header_bytes?: number;
}

export type ElementFieldEnvelope = "max" | "min" | "absmax";

function bucketFor(blob: SidecarElementFieldBlob): FeaManifestFieldPerType {
    return {
        elem_type: "sidecar",
        blob: {
            url: blob.url,
            header_bytes: blob.header_bytes ?? 1024,
            stride_bytes: blob.stride_bytes,
            dtype: "float32",
            byte_order: "little",
        },
    } as FeaManifestFieldPerType;
}

/** One step of an AFEL blob in a plugin's sidecar: the values in payload order (one per row). */
export async function readSidecarElementFieldStep(
    sidecar: SidecarFetcher,
    blob: SidecarElementFieldBlob,
    step: number,
): Promise<Float32Array> {
    const rangeFetcher = async (url: string, start: number, end: number) => {
        const buf = await sidecar.bytes(url, {start, end});
        // A server that ignores Range sends the whole blob; the reader then parses it whole.
        return {buf, ranged: buf.byteLength <= end - start + 1};
    };
    // Keyed on the sidecar's own URL for the blob, so two models' sidecars never share a cache entry.
    return fetchElemFieldStep(rangeFetcher, (url) => sidecar.bytes(url), bucketFor(blob), step, sidecar.url(""));
}

/** The per-row envelope over `steps` (NaN where no step has a value). */
export async function readSidecarElementFieldEnvelope(
    sidecar: SidecarFetcher,
    blob: SidecarElementFieldBlob,
    steps: readonly number[],
    mode: ElementFieldEnvelope = "max",
): Promise<Float32Array | null> {
    let out: Float32Array | null = null;
    for (const step of steps) {
        const values = await readSidecarElementFieldStep(sidecar, blob, step);
        if (!out) out = new Float32Array(values.length).fill(Number.NaN);
        for (let i = 0; i < out.length; i++) {
            const v = mode === "absmax" ? Math.abs(values[i]) : values[i];
            if (!Number.isFinite(v)) continue;
            const cur = out[i];
            if (Number.isNaN(cur) || (mode === "min" ? v < cur : v > cur)) out[i] = v;
        }
    }
    return out;
}

/** `sidecar` with `?v=<token>` on every request, for a sidecar rewritten in place (a re-run writes
 *  the same URLs): the browser cache would otherwise serve the previous run. Null token: unchanged. */
export function withCacheBust(sidecar: SidecarFetcher, token: string | null): SidecarFetcher {
    if (!token) return sidecar;
    const bust = (relKey: string) => `${relKey}${relKey.includes("?") ? "&" : "?"}v=${encodeURIComponent(token)}`;
    return {
        url: (relKey) => sidecar.url(bust(relKey)),
        json: (relKey) => sidecar.json(bust(relKey)),
        bytes: (relKey, range) => sidecar.bytes(bust(relKey), range),
    };
}
