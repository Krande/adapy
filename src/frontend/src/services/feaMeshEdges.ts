// AFEG (Adapy Field EdGe) mesh-edge sidecar parser.
//
// The bake emits a deduped uint32 pair list of element edges
// alongside the mesh GLB. Frontend constructs a THREE.LineSegments
// using the same position attribute as the face mesh, so deformation
// drives both rendering paths from a single buffer.
//
// Format mirrors ada.fem.results.artefacts:
//
//   bytes  content
//   0..3   magic = "AFEG"
//   4..7   uint32 little-endian, version (=1)
//   8..11  uint32 little-endian, n_edges
//   12..15 4-byte zero pad (header total = 16)
//   16..   n_edges × (uint32 from, uint32 to)

import type {FeaFetcher} from "./fea/feaFetcher";

const EDGE_MAGIC = 0x47454641; // "AFEG" little-endian
const EDGE_HEADER_BYTES = 16;
const EDGE_VERSION = 1;

/** Parse an AFEG bytes blob and return a flat uint32 index array
 * suitable for THREE.BufferGeometry.setIndex. The returned view
 * shares the input buffer; callers shouldn't mutate it. */
export function parseMeshEdges(buf: ArrayBuffer): Uint32Array {
    if (buf.byteLength < EDGE_HEADER_BYTES) {
        throw new Error(`mesh edges: too small (${buf.byteLength} bytes)`);
    }
    const dv = new DataView(buf);
    const magic = dv.getUint32(0, true);
    if (magic !== EDGE_MAGIC) {
        throw new Error(`mesh edges: bad magic 0x${magic.toString(16)}`);
    }
    const version = dv.getUint32(4, true);
    if (version !== EDGE_VERSION) {
        throw new Error(`mesh edges: version ${version}, expected ${EDGE_VERSION}`);
    }
    const nEdges = dv.getUint32(8, true);
    if (nEdges === 0) {
        return new Uint32Array(0);
    }
    const expectedPayload = nEdges * 2 * 4;
    if (buf.byteLength < EDGE_HEADER_BYTES + expectedPayload) {
        throw new Error(
            `mesh edges: payload ${buf.byteLength - EDGE_HEADER_BYTES} bytes, ` +
            `expected ${expectedPayload}`,
        );
    }
    return new Uint32Array(buf, EDGE_HEADER_BYTES, nEdges * 2);
}

/** Fetch + parse the mesh-edges sidecar for a baked source.
 *
 * `fetcher` resolves the manifest-relative `edgesUrl` (typically
 * `fea.mesh.edges.bin`) to bytes. See `feaFetcher.ts` for the two
 * standard implementations (`makeViewerApiFetcher` for the standalone
 * viewer; paradoc supplies its own wrapping its REST endpoint). */
export async function fetchMeshEdges(
    fetcher: FeaFetcher,
    edgesUrl: string,
): Promise<Uint32Array> {
    const buf = await fetcher(edgesUrl);
    return parseMeshEdges(buf);
}
