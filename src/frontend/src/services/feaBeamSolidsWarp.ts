// AFBV (Adapy Field Beam Vertices) sidecar parser.
//
// Per-beam-solid-vertex (node0_idx, node1_idx, t) mapping back to
// the main mesh's nodal displacement field. Frontend warp path:
//
//   disp_v = lerp(disp[node0], disp[node1], t)
//
// for every solid-beam vertex v. Without this, scaling a static
// deformation by a large factor leaves the rigid solid beams in
// place while the shells flex — the structure visually
// disconnects. With it the solid mesh deforms in lockstep with its
// parent beam's two endpoints.
//
// Format mirrors ada.fem.results.artefacts:
//
//   bytes  content
//   0..3   magic = "AFBV"
//   4..7   uint32 little-endian, version (=1)
//   8..11  uint32 little-endian, n_verts
//   12..15 4-byte zero pad (header total = 16)
//   16..   n_verts × (uint32 node0_idx, uint32 node1_idx, float32 t)

import type {FeaFetcher} from "./fea/feaFetcher";

const BEAM_WARP_MAGIC = 0x56424641; // "AFBV" little-endian
const BEAM_WARP_HEADER_BYTES = 16;
const BEAM_WARP_ENTRY_BYTES = 12;
const BEAM_WARP_VERSION = 1;

export interface ParsedBeamSolidsWarp {
    /** Number of beam-solid vertices. Must match the GLB's vertex
     *  count or the frontend's lerp loop will read out of bounds. */
    n_verts: number;
    /** Parent-beam node-0 index per vertex (into the main mesh's
     *  point buffer). */
    node0: Uint32Array;
    /** Parent-beam node-1 index per vertex. */
    node1: Uint32Array;
    /** Axial parameter ∈ [0, 1] per vertex. */
    t: Float32Array;
}

export function parseBeamSolidsWarp(buf: ArrayBuffer): ParsedBeamSolidsWarp {
    if (buf.byteLength < BEAM_WARP_HEADER_BYTES) {
        throw new Error(`beam-solid warp: too small (${buf.byteLength} bytes)`);
    }
    const dv = new DataView(buf);
    const magic = dv.getUint32(0, true);
    if (magic !== BEAM_WARP_MAGIC) {
        throw new Error(`beam-solid warp: bad magic 0x${magic.toString(16)}`);
    }
    const version = dv.getUint32(4, true);
    if (version !== BEAM_WARP_VERSION) {
        throw new Error(
            `beam-solid warp: version ${version}, expected ${BEAM_WARP_VERSION}`,
        );
    }
    const nVerts = dv.getUint32(8, true);
    if (nVerts === 0) {
        return {
            n_verts: 0,
            node0: new Uint32Array(0),
            node1: new Uint32Array(0),
            t: new Float32Array(0),
        };
    }
    const expectedPayload = nVerts * BEAM_WARP_ENTRY_BYTES;
    if (buf.byteLength < BEAM_WARP_HEADER_BYTES + expectedPayload) {
        throw new Error(
            `beam-solid warp: payload ${buf.byteLength - BEAM_WARP_HEADER_BYTES} bytes, ` +
            `expected ${expectedPayload}`,
        );
    }
    // Interleaved (n0, n1, t) — each entry is 12 bytes = 3 × uint32.
    // Slice into three typed arrays sharing the same backing buffer
    // so we don't copy. ``t`` reinterprets the third uint32 slot as
    // float32 via a separate Float32Array view on the same offsets.
    const u32 = new Uint32Array(buf, BEAM_WARP_HEADER_BYTES, nVerts * 3);
    const f32 = new Float32Array(buf, BEAM_WARP_HEADER_BYTES, nVerts * 3);

    const node0 = new Uint32Array(nVerts);
    const node1 = new Uint32Array(nVerts);
    const t = new Float32Array(nVerts);
    for (let i = 0; i < nVerts; i++) {
        const base = i * 3;
        node0[i] = u32[base];
        node1[i] = u32[base + 1];
        t[i] = f32[base + 2];
    }
    return {n_verts: nVerts, node0, node1, t};
}

/** Fetch + parse the beam-solids warp sidecar. `fetcher` resolves
 *  the manifest-relative filename to bytes; see `feaFetcher.ts`. */
export async function fetchBeamSolidsWarp(
    fetcher: FeaFetcher,
    warpUrl: string,
): Promise<ParsedBeamSolidsWarp> {
    const buf = await fetcher(warpUrl);
    return parseBeamSolidsWarp(buf);
}
