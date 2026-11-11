// Fading a dense element grid as it is zoomed out, as numbers: zoomed out, a grid draws a line every
// few pixels and washes the result colours out to the edge colour, so its edges are faded by how far
// apart they are on screen. Full strength from `fullPx` apart (a user setting), gone at 3/8 of that.
// Both viewports fade by these numbers. No three.js.

const EDGE_GONE_FRACTION = 3 / 8;

/** How opaque edges `px` CSS pixels apart are drawn. */
export function edgeFadeOpacity(px: number, fullPx: number): number {
    const gonePx = fullPx * EDGE_GONE_FRACTION;
    const t = (px - gonePx) / (fullPx - gonePx);
    return t < 0 ? 0 : t > 1 ? 1 : t;
}

/** The median length of a sample of `edges` (index pairs into `positions`, xyz each), in model units. */
export function typicalEdgeLength(positions: ArrayLike<number>, edges: Uint32Array): number {
    const pairs = edges.length >> 1;
    const step = Math.max(1, Math.floor(pairs / 2000));
    const lengths: number[] = [];
    for (let i = 0; i < pairs; i += step) {
        const a = edges[i * 2] * 3, b = edges[i * 2 + 1] * 3;
        lengths.push(Math.hypot(positions[a] - positions[b], positions[a + 1] - positions[b + 1], positions[a + 2] - positions[b + 2]));
    }
    lengths.sort((x, y) => x - y);
    return lengths[Math.floor(lengths.length / 2)] ?? 0;
}

/**
 * CSS pixels per model unit where the user is looking, under a perspective camera: at `target`
 * clamped into the model's box (min xyz, max xyz), seen from `eye`. The density is taken there and
 * not at the model's centre, or the same standoff would show the grid from one side only when the
 * point orbited is off the centre.
 */
export function pixelsPerUnitAtFocus(
    eye: ArrayLike<number>, target: ArrayLike<number>, box: ArrayLike<number>, fovDeg: number, viewHeightPx: number,
): number {
    let d2 = 0;
    for (let k = 0; k < 3; k++) {
        const focus = Math.min(Math.max(target[k], box[k]), box[k + 3]);
        d2 += (focus - eye[k]) * (focus - eye[k]);
    }
    return viewHeightPx / (2 * Math.max(Math.sqrt(d2), 1e-9) * Math.tan((fovDeg * Math.PI) / 360));
}
