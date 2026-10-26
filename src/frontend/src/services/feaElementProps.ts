// AFEP (Adapy Fea Element Properties) sidecar: what a picked element IS.
//
// Format mirrors ada.fem.results.artefacts (``write_element_properties``):
//
//   bytes  content
//   0..3   magic = "AFEP"
//   4..7   uint32 little-endian, version (=1)
//   8..11  uint32 little-endian, n_elements
//   12..15 4-byte zero pad (header total = 16)
//   16..   n_elements × (uint32 label, uint32 eltype, uint32 material,
//                        uint32 section, float32 thickness), sorted by label
//
// The material / section keys index the manifest's ``element_properties``
// tables (0 = none); thickness is NaN where the element has none. Fetched once
// per source, on the first click that wants it, and searched by label.

import type {FeaFetcher} from "./fea/feaFetcher";

const MAGIC = 0x50454641; // "AFEP" little-endian
const HEADER_BYTES = 16;
const VERSION = 1;
const ENTRY_WORDS = 5;

export interface FeaElementProps {
    label: number;
    eltype: number;
    material: number;
    section: number;
    /** NaN when the element has none. */
    thickness: number;
}

export interface FeaElementPropsTable {
    /** Look an element up by the label the viewer shows. */
    get(label: number): FeaElementProps | null;
    readonly size: number;
}

export function parseElementProps(buf: ArrayBuffer): FeaElementPropsTable {
    if (buf.byteLength < HEADER_BYTES) throw new Error(`element props: too small (${buf.byteLength} bytes)`);
    const dv = new DataView(buf);
    const magic = dv.getUint32(0, true);
    if (magic !== MAGIC) throw new Error(`element props: bad magic 0x${magic.toString(16)}`);
    const version = dv.getUint32(4, true);
    if (version !== VERSION) throw new Error(`element props: version ${version}, expected ${VERSION}`);
    const n = dv.getUint32(8, true);
    if (buf.byteLength < HEADER_BYTES + n * ENTRY_WORDS * 4) {
        throw new Error(`element props: payload shorter than ${n} entries`);
    }
    const words = new Uint32Array(buf, HEADER_BYTES, n * ENTRY_WORDS);
    const floats = new Float32Array(buf, HEADER_BYTES, n * ENTRY_WORDS);
    return {
        size: n,
        get(label: number): FeaElementProps | null {
            let lo = 0;
            let hi = n - 1;
            while (lo <= hi) {
                const mid = (lo + hi) >>> 1;
                const at = words[mid * ENTRY_WORDS];
                if (at === label) {
                    const o = mid * ENTRY_WORDS;
                    return {
                        label,
                        eltype: words[o + 1],
                        material: words[o + 2],
                        section: words[o + 3],
                        thickness: floats[o + 4],
                    };
                }
                if (at < label) lo = mid + 1;
                else hi = mid - 1;
            }
            return null;
        },
    };
}

// The loaded source's sidecar only. One FEA result is open at a time, and a few MB per
// source kept for every result ever opened in a session is a leak, not a cache.
let cached: {key: string; pending: Promise<FeaElementPropsTable | null>} | null = null;

/** The sidecar for `cacheKey` (one per loaded source), fetched once. Null when the
 *  bake has none or it cannot be read: the panel then shows what it can without it. */
export function fetchElementProps(
    fetcher: FeaFetcher,
    url: string,
    cacheKey: string,
): Promise<FeaElementPropsTable | null> {
    if (cached?.key === cacheKey) return cached.pending;
    const pending = fetcher(url)
        .then(parseElementProps)
        .catch((err) => {
            // eslint-disable-next-line no-console
            console.warn("[fea] element properties unavailable:", err);
            // A failed read is not remembered: the next click tries again.
            if (cached?.key === cacheKey) cached = null;
            return null;
        });
    cached = {key: cacheKey, pending};
    return pending;
}
