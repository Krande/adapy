// AFEM (Adapy Field Element) mesh-element sidecar parser.
//
// Per-element draw-range list paired with the mesh GLB. Frontend
// hydrates these into userdata.id_hierarchy +
// userdata.draw_ranges_<meshName> so the FEA mesh slots into the
// existing CustomBatchedMesh pick + highlight pipeline.
//
// Format mirrors ada.fem.results.artefacts:
//
//   bytes  content
//   0..3   magic = "AFEM"
//   4..7   uint32 little-endian, version (=1)
//   8..11  uint32 little-endian, n_elements
//   12..15 4-byte zero pad (header total = 16)
//   16..   n_elements × (uint32 label, uint32 tri_start, uint32 tri_count)

import type {FeaFetcher} from "./fea/feaFetcher";

const ELEM_MAGIC = 0x4d454641; // "AFEM" little-endian
const ELEM_HEADER_BYTES = 16;
const ELEM_ENTRY_BYTES = 12;
const ELEM_VERSION = 1;

export interface MeshElementEntry {
    /** Source-file element label (RMED MAI/<type>/NUM, SIF id, etc.). */
    label: number;
    /** Triangle index within the flat triangle buffer of fea.mesh.glb. */
    triStart: number;
    /** Triangle count owned by this element. Line elements get 0. */
    triCount: number;
}

/** Parse an AFEM bytes blob and return the per-element entries.
 * Each tri_start/tri_count is in *triangles*, not vertex indices —
 * multiply by 3 for the index-buffer offset. */
export function parseMeshElements(buf: ArrayBuffer): MeshElementEntry[] {
    if (buf.byteLength < ELEM_HEADER_BYTES) {
        throw new Error(`mesh elements: too small (${buf.byteLength} bytes)`);
    }
    const dv = new DataView(buf);
    const magic = dv.getUint32(0, true);
    if (magic !== ELEM_MAGIC) {
        throw new Error(`mesh elements: bad magic 0x${magic.toString(16)}`);
    }
    const version = dv.getUint32(4, true);
    if (version !== ELEM_VERSION) {
        throw new Error(`mesh elements: version ${version}, expected ${ELEM_VERSION}`);
    }
    const nElements = dv.getUint32(8, true);
    if (nElements === 0) {
        return [];
    }
    const expectedPayload = nElements * ELEM_ENTRY_BYTES;
    if (buf.byteLength < ELEM_HEADER_BYTES + expectedPayload) {
        throw new Error(
            `mesh elements: payload ${buf.byteLength - ELEM_HEADER_BYTES} bytes, ` +
            `expected ${expectedPayload}`,
        );
    }
    const arr = new Uint32Array(buf, ELEM_HEADER_BYTES, nElements * 3);
    const out: MeshElementEntry[] = new Array(nElements);
    for (let i = 0; i < nElements; i++) {
        out[i] = {
            label: arr[i * 3 + 0],
            triStart: arr[i * 3 + 1],
            triCount: arr[i * 3 + 2],
        };
    }
    return out;
}

/** Fetch + parse the mesh-elements sidecar for a baked source.
 *
 * `fetcher` resolves the manifest-relative `elementsUrl` to bytes.
 * See `feaFetcher.ts` for storage-layer wrappers. */
export async function fetchMeshElements(
    fetcher: FeaFetcher,
    elementsUrl: string,
): Promise<MeshElementEntry[]> {
    const buf = await fetcher(elementsUrl);
    return parseMeshElements(buf);
}
