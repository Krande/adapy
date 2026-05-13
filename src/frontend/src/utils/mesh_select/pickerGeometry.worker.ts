// Worker that builds the GPU picker's per-mesh geometry buffers off the
// main thread. For a 3M-tri FEA mesh the per-triangle position/colour
// fan-out (and per-morph-target duplication) is 50–200ms of straight
// memory writes — small ops, but the volume blocks the main thread long
// enough to drop a frame or two on the first click after a model load.
// Moving the loop here keeps the viewer interactive during registration;
// the only main-thread cost left is the input ``.slice()`` copies (so
// transferring doesn't detach the source mesh's GPU-bound buffers) and
// the cheap ``BufferAttribute`` wrap on the response.
//
// All input typed arrays are transferred in; all output typed arrays
// are transferred back. No three.js code lives here — we build raw
// typed arrays and let the main thread wrap them in BufferAttributes.

import * as Comlink from "comlink";

export interface PickerBuildInput {
    flat: boolean;
    // Triangle indices into ``posArr``. Uint16 or Uint32, whichever the
    // source BufferAttribute used. The worker doesn't care about width.
    indices: Uint16Array | Uint32Array;
    // Source position attribute array (typically 3 floats per vertex).
    posArr: Float32Array;
    itemSize: number;
    nTris: number;
    // Distinct vertices in ``posArr`` — only needed for the flat
    // builder, ignored by the non-indexed path.
    nOrigVerts: number;
    // Per-triangle 8-bit pickColor (r, g, b) — built on main from the
    // global id counter so id allocation stays sequential across all
    // registered meshes.
    triColor: Uint8Array;
    // Source morph position attribute arrays, one per morph target.
    // Empty array if the mesh has no morphs.
    morphArrs: Float32Array[];
    morphItemSize: number;
    morphTargetsRelative: boolean;
}

export interface PickerBuildOutput {
    positions: Float32Array;
    colors: Uint8Array;
    // null for the non-indexed builder; Uint32Array index buffer for
    // the flat builder.
    indices: Uint32Array | null;
    // Per-picker-vertex source-vertex index. The main thread keeps this
    // around so it can refresh morph deltas in-place when ``applyField``
    // swaps the source attrs.
    sourceVertexIndex: Uint32Array;
    morphArrs: Float32Array[];
    byteLength: number;
    morphTargetsRelative: boolean;
}

function duplicateMorph(
    srcArr: Float32Array,
    srcItem: number,
    sourceVertexIndex: Uint32Array,
): Float32Array {
    const nPickerVerts = sourceVertexIndex.length;
    const dup = new Float32Array(nPickerVerts * 3);
    for (let p = 0; p < nPickerVerts; p++) {
        const s = sourceVertexIndex[p] * srcItem;
        const o = p * 3;
        dup[o + 0] = srcArr[s];
        dup[o + 1] = srcArr[s + 1];
        dup[o + 2] = srcArr[s + 2];
    }
    return dup;
}

function buildNonIndexed(input: PickerBuildInput): PickerBuildOutput {
    const {
        indices, posArr, itemSize, nTris, triColor,
        morphArrs, morphItemSize, morphTargetsRelative,
    } = input;

    const positions = new Float32Array(nTris * 9);
    const colors = new Uint8Array(nTris * 9);
    const sourceVertexIndex = new Uint32Array(nTris * 3);
    for (let ti = 0; ti < nTris; ti++) {
        const v0 = indices[ti * 3];
        const v1 = indices[ti * 3 + 1];
        const v2 = indices[ti * 3 + 2];
        const i0 = v0 * itemSize;
        const i1 = v1 * itemSize;
        const i2 = v2 * itemSize;
        const offP = ti * 9;
        positions[offP + 0] = posArr[i0];
        positions[offP + 1] = posArr[i0 + 1];
        positions[offP + 2] = posArr[i0 + 2];
        positions[offP + 3] = posArr[i1];
        positions[offP + 4] = posArr[i1 + 1];
        positions[offP + 5] = posArr[i1 + 2];
        positions[offP + 6] = posArr[i2];
        positions[offP + 7] = posArr[i2 + 1];
        positions[offP + 8] = posArr[i2 + 2];

        sourceVertexIndex[ti * 3 + 0] = v0;
        sourceVertexIndex[ti * 3 + 1] = v1;
        sourceVertexIndex[ti * 3 + 2] = v2;

        const r = triColor[ti * 3];
        const g = triColor[ti * 3 + 1];
        const b = triColor[ti * 3 + 2];
        colors[offP + 0] = r;
        colors[offP + 1] = g;
        colors[offP + 2] = b;
        colors[offP + 3] = r;
        colors[offP + 4] = g;
        colors[offP + 5] = b;
        colors[offP + 6] = r;
        colors[offP + 7] = g;
        colors[offP + 8] = b;
    }

    const dupMorphs: Float32Array[] = [];
    let morphBytes = 0;
    for (const src of morphArrs) {
        const dup = duplicateMorph(src, morphItemSize, sourceVertexIndex);
        dupMorphs.push(dup);
        morphBytes += dup.byteLength;
    }

    return {
        positions,
        colors,
        indices: null,
        sourceVertexIndex,
        morphArrs: dupMorphs,
        morphTargetsRelative,
        byteLength: positions.byteLength + colors.byteLength + morphBytes,
    };
}

function buildFlat(input: PickerBuildInput): PickerBuildOutput {
    const {
        indices, posArr, itemSize, nTris, nOrigVerts, triColor,
        morphArrs, morphItemSize, morphTargetsRelative,
    } = input;

    const nPickerVerts = nOrigVerts + nTris;
    const positions = new Float32Array(nPickerVerts * 3);
    const colors = new Uint8Array(nPickerVerts * 3);
    const pickerIndices = new Uint32Array(nTris * 3);
    const sourceVertexIndex = new Uint32Array(nPickerVerts);

    for (let v = 0; v < nOrigVerts; v++) {
        const s = v * itemSize;
        const o = v * 3;
        positions[o + 0] = posArr[s];
        positions[o + 1] = posArr[s + 1];
        positions[o + 2] = posArr[s + 2];
        sourceVertexIndex[v] = v;
    }

    for (let ti = 0; ti < nTris; ti++) {
        const a = indices[ti * 3];
        const b = indices[ti * 3 + 1];
        const c = indices[ti * 3 + 2];
        const newIdx = nOrigVerts + ti;

        const cs = c * itemSize;
        const no = newIdx * 3;
        positions[no + 0] = posArr[cs];
        positions[no + 1] = posArr[cs + 1];
        positions[no + 2] = posArr[cs + 2];

        colors[no + 0] = triColor[ti * 3];
        colors[no + 1] = triColor[ti * 3 + 1];
        colors[no + 2] = triColor[ti * 3 + 2];

        pickerIndices[ti * 3 + 0] = a;
        pickerIndices[ti * 3 + 1] = b;
        pickerIndices[ti * 3 + 2] = newIdx;

        sourceVertexIndex[newIdx] = c;
    }

    const dupMorphs: Float32Array[] = [];
    let morphBytes = 0;
    for (const src of morphArrs) {
        const dup = duplicateMorph(src, morphItemSize, sourceVertexIndex);
        dupMorphs.push(dup);
        morphBytes += dup.byteLength;
    }

    return {
        positions,
        colors,
        indices: pickerIndices,
        sourceVertexIndex,
        morphArrs: dupMorphs,
        morphTargetsRelative,
        byteLength:
            positions.byteLength
            + colors.byteLength
            + pickerIndices.byteLength
            + morphBytes,
    };
}

const api = {
    build(input: PickerBuildInput): PickerBuildOutput {
        const out = input.flat ? buildFlat(input) : buildNonIndexed(input);
        // ``.buffer`` on a typed array is typed ``ArrayBufferLike`` in
        // current TS lib.dom (covers both ArrayBuffer and SharedArrayBuffer);
        // Comlink.transfer wants plain ``ArrayBuffer``. We only ever
        // construct from plain ArrayBuffers here, so the cast is safe.
        const transfers: ArrayBuffer[] = [
            out.positions.buffer as ArrayBuffer,
            out.colors.buffer as ArrayBuffer,
            out.sourceVertexIndex.buffer as ArrayBuffer,
        ];
        if (out.indices) transfers.push(out.indices.buffer as ArrayBuffer);
        for (const m of out.morphArrs) transfers.push(m.buffer as ArrayBuffer);
        return Comlink.transfer(out, transfers);
    },
};

export type PickerGeometryWorkerAPI = typeof api;

Comlink.expose(api);
