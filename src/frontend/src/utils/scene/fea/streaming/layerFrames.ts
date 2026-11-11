// FEA streaming: the played frames of a result drawn through result layers, kept to be shown again.
//
// Owns: what one painted frame of a time history is as plain data -- the displacement per node,
// the colours per vertex of the faces as bytes, the same for the beam sections, and the colours
// and displacement of the result points and of the coloured lines of line elements -- kept by step
// so the next pass through the history writes it to the layers without fetching or painting the
// step again. The three.js loader records the same things (../playbackFrames); there the
// displacement goes to a texture, here a layer takes it as it takes any step's.
//
// Where the result points and the coloured lines are is kept once, from the first frame kept: a
// paint leaves out a point whose value is not a number, so a frame whose points are elsewhere is
// not kept and is painted again each pass.
//
// A fixed number of frames, chosen from the step cache's budget when the recording starts; the
// step cache is shrunk by what the recording reserves, so the two stay within one budget. Frames
// past the capacity keep painting the ordinary way.
//
// No three.js in this module or in anything it imports.

import {FEA_STEP_CACHE} from "@/services/fea/stepCache";
import type {PaintedPoints} from "../elemFieldPaint";

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
    /** The result points and the coloured lines the paint drew beside the faces; none when left out. */
    markers?: PaintedPoints | null;
    lines?: PaintedPoints | null;
}

/** A frame as kept: the displacements as they were, the colours as bytes. */
export interface RecordedFrame {
    displacement: Float32Array;
    colors: Uint8Array;
    solidColors: Uint8Array | null;
    solidDisplacement: Float32Array | null;
    markerColors: Uint8Array | null;
    markerDisplacement: Float32Array | null;
    lineColors: Uint8Array | null;
    lineDisplacement: Float32Array | null;
}

/** The points every frame of a recording was painted with (result points, coloured lines). */
export interface FramePoints {
    markers: PaintedPoints | null;
    lines: PaintedPoints | null;
}

const NO_POINTS: FramePoints = {markers: null, lines: null};

/** Points that are drawn, or null. */
const drawn = (points: PaintedPoints | null | undefined): PaintedPoints | null => (points && points.positions.length > 0 ? points : null);

/** Are `now` the points `kept` are: both none, or at the same places. */
function samePoints(kept: PaintedPoints | null, now: PaintedPoints | null): boolean {
    if (!kept || !now) return kept === now;
    const a = kept.positions, b = now.positions;
    if (a === b) return true;
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
}

/** Where a recording's points are, with buffers a kept frame is read back into. */
function keptPoints(points: PaintedPoints | null): PaintedPoints | null {
    if (!points) return null;
    return {positions: points.positions, labels: points.labels, colors: new Float32Array(points.positions.length), displacement: points.displacement};
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
    /** The result points and the coloured lines of every frame kept. */
    private readonly markers: PaintedPoints | null;
    private readonly lines: PaintedPoints | null;

    /** `key` names what the frames are valid for; `shape` the vertex counts they were painted
     *  with (nodes, vertices of the faces as drawn, vertices of the beam sections while those are
     *  drawn): a recording is for one of each. `points` are the result points and the coloured
     *  lines its frames have, as the first of them was painted. */
    constructor(
        readonly key: string,
        readonly shape: readonly [nodes: number, faceVertices: number, solidVertices: number],
        nFrames: number,
        budgetBytes: number = FEA_STEP_CACHE.budgetBytes,
        points: FramePoints = NO_POINTS,
    ) {
        const [nodes, faceVertices, solidVertices] = shape;
        this.markers = keptPoints(drawn(points.markers));
        this.lines = keptPoints(drawn(points.lines));
        // A point keeps what a vertex of the beam sections does: a colour and a displacement.
        const pointCount = ((this.markers?.positions.length ?? 0) + (this.lines?.positions.length ?? 0)) / 3;
        const perFrame = nodes * 12 + faceVertices * 3 + (solidVertices + pointCount) * 15;
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
     *  the frame is not of the shape it was made for: other vertex counts, or result points or
     *  coloured lines that are not where the recording's are. */
    record(step: number, frame: PaintedFrame): boolean {
        if (this.disposed || this.frames.has(step) || this.frames.size >= this.capacity) return false;
        const [nodes, faceVertices, solidVertices] = this.shape;
        if (frame.displacement.length !== nodes * 3 || frame.colors.length !== faceVertices * 3) return false;
        const markers = drawn(frame.markers), lines = drawn(frame.lines);
        if (!samePoints(this.markers, markers) || !samePoints(this.lines, lines)) return false;
        const solids = solidVertices > 0;
        this.frames.set(step, {
            displacement: frame.displacement.slice(),
            colors: toBytes(frame.colors, new Uint8Array(faceVertices * 3)),
            // The sections' colours are in whatever numbering they were painted in.
            solidColors: solids && frame.solidColors ? toBytes(frame.solidColors, new Uint8Array(frame.solidColors.length)) : null,
            solidDisplacement: solids && frame.solidDisplacement?.length === solidVertices * 3 ? frame.solidDisplacement.slice() : null,
            markerColors: markers ? toBytes(markers.colors, new Uint8Array(markers.colors.length)) : null,
            markerDisplacement: markers ? markers.displacement.slice() : null,
            lineColors: lines ? toBytes(lines.colors, new Uint8Array(lines.colors.length)) : null,
            lineDisplacement: lines ? lines.displacement.slice() : null,
        });
        return true;
    }

    /** The result points (or the coloured lines) of a kept frame as a paint hands them over; null
     *  when the recording has none. The same object each call, read before the next one. */
    points(kind: "markers" | "lines", frame: RecordedFrame): PaintedPoints | null {
        const kept = kind === "markers" ? this.markers : this.lines;
        const colors = kind === "markers" ? frame.markerColors : frame.lineColors;
        const displacement = kind === "markers" ? frame.markerDisplacement : frame.lineDisplacement;
        if (!kept || !colors || !displacement) return null;
        colorsFromBytes(colors, kept.colors);
        kept.displacement = displacement;
        return kept;
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
