// Worker that expands the compact beam-solid artefact (AFBS) into the mesh
// buffers off the main thread.
//
// The work is small per beam — two rings of a cached outline and one triangle
// copy — but a large deck has 165k of them, so it is tens of millions of
// straight memory writes: the same shape of cost as the GPU picker's
// per-triangle fan-out, and the same reason to move it here. The alternative
// is not "no work": it is the GLTFLoader parsing an 88 MB GLB on the main
// thread, which is what this replaces.
//
// The compact buffer is transferred IN (the caller has no further use for it)
// and every output array is transferred back. No three.js lives here — the
// main thread wraps the raw typed arrays in BufferAttributes, same division of
// labour as pickerGeometry.worker.

import * as Comlink from "comlink";

import {
    expandBeamSolidsFromBytes,
    type ExpandedBeamSolids,
} from "@/services/feaBeamSolidsCompact";

export interface BeamSolidsExpandInput {
    /** The raw AFBS bytes. Transferred in — do not touch it afterwards. */
    compact: ArrayBuffer;
    /** The main FEA mesh's position buffer, which `node0`/`node1` index.
     *  A copy, so transferring it never detaches the live mesh's attribute. */
    mainPositions: Float32Array;
}

export type BeamSolidsExpandOutput = ExpandedBeamSolids;

const api = {
    async expand(input: BeamSolidsExpandInput): Promise<BeamSolidsExpandOutput> {
        // Async because the expansion is the adacpp wasm module now, which has to
        // be instantiated first. Comlink already returns a promise either way, so
        // the caller is unchanged.
        const out = await expandBeamSolidsFromBytes(input.compact, input.mainPositions);
        // ``.buffer`` is typed ArrayBufferLike (it covers SharedArrayBuffer
        // too); every array here is freshly allocated from a plain
        // ArrayBuffer, so the cast is safe. Same note as pickerGeometry.worker.
        const transfers: ArrayBuffer[] = [
            out.positions.buffer as ArrayBuffer,
            out.indices.buffer as ArrayBuffer,
            out.elemLabel.buffer as ArrayBuffer,
            out.elemTriStart.buffer as ArrayBuffer,
            out.elemTriCount.buffer as ArrayBuffer,
            out.node0.buffer as ArrayBuffer,
            out.node1.buffer as ArrayBuffer,
            out.t.buffer as ArrayBuffer,
        ];
        return Comlink.transfer(out, transfers);
    },
};

export type BeamSolidsExpandWorkerAPI = typeof api;

Comlink.expose(api);
