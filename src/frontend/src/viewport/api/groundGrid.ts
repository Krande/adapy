// The ground grid every viewport draws, as plain numbers: its size, divisions and colours, and its lines.
// It lies on the ground plane through the origin (XY in a Z-up world, XZ in a Y-up one). No three.js.

export const GROUND_GRID = {
    size: 100,
    divisions: 100,
    /** The two lines through the origin. */
    centreColor: "#888888",
    lineColor: "#444444",
} as const;

/** Line segments (xyz per point, two points per segment): the grid's lines, and its two through the
 *  origin, laid as three.js' GridHelper lays them. */
export function groundGridLines(zIsUp: boolean, spec: {size: number; divisions: number} = GROUND_GRID): {lines: Float32Array; centre: Float32Array} {
    const half = spec.size / 2;
    const step = spec.size / spec.divisions;
    const centreIndex = spec.divisions / 2;
    const lines: number[] = [], centre: number[] = [];
    // The plane's two axes: x, and y (Z-up) or z (Y-up).
    const point = (a: number, b: number): [number, number, number] => (zIsUp ? [a, b, 0] : [a, 0, b]);
    for (let i = 0; i <= spec.divisions; i++) {
        const k = -half + i * step;
        const out = i === centreIndex ? centre : lines;
        out.push(...point(-half, k), ...point(half, k));
        out.push(...point(k, -half), ...point(k, half));
    }
    return {lines: Float32Array.from(lines), centre: Float32Array.from(centre)};
}
