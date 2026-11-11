// FEA streaming: the played frames of a result drawn through result layers, kept to be shown again.
//
// Owns: what one painted frame of a time history is as plain data -- the displacement per node,
// the colours per vertex of the faces as bytes, and the same for the beam sections -- kept by step
// so the next pass through the history writes it to the layers without fetching or painting the
// step again. The three.js loader records the same things (../playbackFrames); there the
// displacement goes to a texture, here a layer takes it as it takes any step's.
//
// A fixed number of frames, chosen from the step cache's budget when the recording starts; the
// step cache is shrunk by what the recording reserves, so the two stay within one budget. Frames
// past the capacity keep painting the ordinary way.
//
// No three.js in this module or in anything it imports.

import {FEA_STEP_CACHE} from "@/services/fea/stepCache";

/** Share of the step-cache budget the recording may take. */
const BUDGET_SHARE = 0.5;

/** One frame as the paint left it. Every array is the session's own buffer: read during the call. */
export interface PaintedFrame {
    /** The displacement per node, xyz, not scaled. */
    displacement: Float32Array;
    /** The colours of the faces, rgb per vertex in the numbering the faces are drawn in. */
    colors: Float32Array;
    /** The beam sections' colours (rgb per vertex of theirs) and displacement, while they are drawn. */
    solidColors: Float32Array | null;
    solidDisplacement: Float32Array | null;
}

/** A frame as kept: the displacements as they were, the colours as bytes. */
export interface RecordedFrame {
    displacement: Float32Array;
    colors: Uint8Array;
    solidColors: Uint8Array | null;
    solidDisplacement: Float32Array | null;
}

/** Colours in 0..1 as bytes. */
function toBytes(src: Float32Array, out: Uint8Array): Uint8Array {
    for (let i = 0; i < out.length; i++) {
        const v = src[i];
        out[i] = v <= 0 ? 0 : v >= 1 ? 255 : Math.round(v * 255);
    }
    return out;
}

/** Bytes as colours in 0..1, into `out`. */
export function colorsFromBytes(src: Uint8Array, out: Float32Array): Float32Array {
    const inv = 1 / 255;
    const n = Math.min(src.length, out.length);
    for (let i = 0; i < n; i++) out[i] = src[i] * inv;
    return out;
}

export class LayerFrameRecorder {
    readonly capacity: number;
    private readonly frames = new Map<number, RecordedFrame>();
    private readonly baseBudget: number;
    private readonly reserved: number;
    private disposed = false;

    /** `key` names what the frames are valid for; `shape` the vertex counts they were painted
     *  with (nodes, vertices of the faces as drawn, vertices of the beam sections while those are
     *  drawn): a recording is for one of each. */
    constructor(
        readonly key: string,
        readonly shape: readonly [nodes: number, faceVertices: number, solidVertices: number],
        nFrames: number,
        budgetBytes: number = FEA_STEP_CACHE.budgetBytes,
    ) {
        const [nodes, faceVertices, solidVertices] = shape;
        const perFrame = nodes * 12 + faceVertices * 3 + solidVertices * 15;
        this.capacity = Math.max(0, Math.min(nFrames, Math.floor((budgetBytes * BUDGET_SHARE) / Math.max(1, perFrame))));
        // What the recording reserves comes out of the step cache's budget.
        this.baseBudget = FEA_STEP_CACHE.budgetBytes;
        this.reserved = this.capacity * perFrame;
        FEA_STEP_CACHE.setBudget(Math.max(0, this.baseBudget - this.reserved));
    }

    get size(): number {
        return this.frames.size;
    }

    has(step: number): boolean {
        return this.frames.has(step);
    }

    /** Is this the recording for `key` and these vertex counts. */
    matches(key: string, shape: readonly [number, number, number]): boolean {
        return !this.disposed && key === this.key && shape[0] === this.shape[0] && shape[1] === this.shape[1] && shape[2] === this.shape[2];
    }

    /** Keep the frame just painted. False when the recording is full, has the step already, or
     *  the frame is not of the shape it was made for. */
    record(step: number, frame: PaintedFrame): boolean {
        if (this.disposed || this.frames.has(step) || this.frames.size >= this.capacity) return false;
        const [nodes, faceVertices, solidVertices] = this.shape;
        if (frame.displacement.length !== nodes * 3 || frame.colors.length !== faceVertices * 3) return false;
        const solids = solidVertices > 0;
        this.frames.set(step, {
            displacement: frame.displacement.slice(),
            colors: toBytes(frame.colors, new Uint8Array(faceVertices * 3)),
            // The sections' colours are in whatever numbering they were painted in.
            solidColors: solids && frame.solidColors ? toBytes(frame.solidColors, new Uint8Array(frame.solidColors.length)) : null,
            solidDisplacement: solids && frame.solidDisplacement?.length === solidVertices * 3 ? frame.solidDisplacement.slice() : null,
        });
        return true;
    }

    get(step: number): RecordedFrame | null {
        return this.disposed ? null : this.frames.get(step) ?? null;
    }

    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        this.frames.clear();
        FEA_STEP_CACHE.setBudget(this.baseBudget);
    }
}
