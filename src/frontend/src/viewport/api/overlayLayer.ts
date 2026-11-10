// Lines and points drawn over the model: what outlines, markers and a feature's own annotations are
// made of. A feature describes them in plain data (segments or points, a colour, a width or a size)
// and the viewport on screen draws them, three.js as wide lines in its scene and the three.js-free
// viewport as geometry of the renderer. Beside the billboards (./billboards.ts), which are the
// images. Nothing here (or anything it imports at runtime) may import three.js.

/** How an overlay is drawn, whatever it is made of. */
export interface OverlayLook {
    /** One colour for all of it, linear RGB in 0..1. White when left out. */
    color?: readonly [number, number, number];
    /** How opaque it is, 0..1; fully opaque when left out. */
    opacity?: number;
    /** Drawn over the model instead of hidden behind what is in front of it. */
    onTop?: boolean;
    /** Cut by the section planes, as the model is. */
    clipped?: boolean;
    /** Where the coordinates stand in the world: a column-major 4x4, identity when absent. */
    matrix?: ArrayLike<number> | null;
}

export interface OverlayLines extends OverlayLook {
    /** Six numbers per segment: x0, y0, z0, x1, y1, z1. */
    segments: Float32Array;
    /** A colour per segment (linear RGB, three numbers each) instead of `color`. */
    colors?: Float32Array;
    /** In CSS pixels; 2 when left out. */
    widthPx?: number;
    /** Dashes, in the units of the coordinates. */
    dashed?: {dash: number; gap: number};
}

export interface OverlayPoints extends OverlayLook {
    /** Three numbers per point. */
    positions: Float32Array;
    /** A colour per point (linear RGB, three numbers each) instead of `color`. */
    colors?: Float32Array;
    /** The diameter in CSS pixels; 6 when left out. */
    sizePx?: number;
}

/** One feature's lines and points in a viewport. They take no part in picking the model's objects.
 *  The arrays handed in are read during the call and not kept. */
export interface OverlayLayer {
    /** Returns the handle `setPositions` and `remove` take. */
    addLines(lines: OverlayLines): number;
    addPoints(points: OverlayPoints): number;
    /** Move what `handle` draws: as many numbers as it was added with (a line's `segments`, a
     *  point set's `positions`). Colours and look stay. */
    setPositions(handle: number, positions: Float32Array): void;
    remove(handle: number): void;
    clear(): void;
    /** Remove everything and release what the layer holds; the layer is not used again. */
    dispose(): void;
}

/** A layer that draws nothing: what a caller without a viewport gets. */
export const NO_OVERLAYS: OverlayLayer = {
    addLines: () => 0,
    addPoints: () => 0,
    setPositions: () => {},
    remove: () => {},
    clear: () => {},
    dispose: () => {},
};

/** The length along each segment at its two ends (0, then the segment's length): what dashes are
 *  measured by. Two numbers per segment into `out`. */
export function segmentDistances(segments: Float32Array, out: Float32Array): Float32Array {
    for (let s = 0, i = 0; i + 5 < segments.length; s += 2, i += 6) {
        out[s] = 0;
        out[s + 1] = Math.hypot(segments[i + 3] - segments[i], segments[i + 4] - segments[i + 1], segments[i + 5] - segments[i + 2]);
    }
    return out;
}

/** A colour per segment as a colour per end point (two for each segment), into `out`. */
export function colorsPerEndpoint(perSegment: ArrayLike<number>, out: Float32Array): Float32Array {
    for (let s = 0, o = 0; s + 2 < perSegment.length; s += 3, o += 6) {
        out[o] = out[o + 3] = perSegment[s];
        out[o + 1] = out[o + 4] = perSegment[s + 1];
        out[o + 2] = out[o + 5] = perSegment[s + 2];
    }
    return out;
}
