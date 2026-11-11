// FEA streaming: which line element is under a point of the view, for a result drawn through
// result layers. A renderer that picks triangles does not see a beam drawn as a line; the lines are
// plain data here, so the one under the cursor is found on the screen. No three.js.

/** A line is hit within this many CSS pixels, as on the three.js viewport. */
const REACH_PX = 4;

export type Projector = (world: Float32Array, count: number, out: Float32Array) => unknown;

export interface LinePickInput {
    /** Two points per line, xyz each, with the displacement of each and how much of it is drawn. */
    positions: Float32Array;
    displacement: Float32Array;
    influence: number;
    /** Where the points stand in the world: a column-major 4x4, identity when absent. */
    matrix?: ArrayLike<number> | null;
    /** Whether line `i` is left out (its element is hidden). */
    skip?: (line: number) => boolean;
}

/** The line nearest the point (x, y) within reach, with the depth of its nearest point (0 near,
 *  1 far); null when none is. Of several within reach, the one nearest the camera. */
export function lineAt(input: LinePickInput, project: Projector, x: number, y: number): {line: number; depth: number} | null {
    const {positions, displacement, influence, matrix: m} = input;
    const points = positions.length / 3;
    const world = new Float32Array(points * 3), screen = new Float32Array(points * 3);
    for (let i = 0; i < points * 3; i += 3) {
        const px = positions[i] + influence * displacement[i];
        const py = positions[i + 1] + influence * displacement[i + 1];
        const pz = positions[i + 2] + influence * displacement[i + 2];
        if (m) {
            world[i] = m[0] * px + m[4] * py + m[8] * pz + m[12];
            world[i + 1] = m[1] * px + m[5] * py + m[9] * pz + m[13];
            world[i + 2] = m[2] * px + m[6] * py + m[10] * pz + m[14];
        } else {
            world[i] = px, world[i + 1] = py, world[i + 2] = pz;
        }
    }
    project(world, points, screen);
    let best: {line: number; depth: number} | null = null;
    for (let line = 0, o = 0; o + 5 < screen.length; line++, o += 6) {
        const ax = screen[o], ay = screen[o + 1], bx = screen[o + 3], by = screen[o + 4];
        if (!(ax === ax && bx === bx)) continue; // behind the camera
        const ux = bx - ax, uy = by - ay, len2 = ux * ux + uy * uy;
        const t = len2 > 0 ? Math.max(0, Math.min(1, ((x - ax) * ux + (y - ay) * uy) / len2)) : 0;
        if (Math.hypot(x - ax - ux * t, y - ay - uy * t) > REACH_PX) continue;
        const depth = screen[o + 2] + (screen[o + 5] - screen[o + 2]) * t;
        if (!(depth >= 0 && depth <= 1) || (best && depth >= best.depth)) continue;
        if (input.skip?.(line)) continue;
        best = {line, depth};
    }
    return best;
}

/** Is something at `depth` in front of a surface at `surfaceDepth`? With a small allowance, because
 *  a stiffener modelled on a plate lies exactly on it and rounding would otherwise pick the winner:
 *  0.2 % of the distance from the camera, which is this share of what is left of the depth range. */
export function inFrontOf(depth: number, surfaceDepth: number): boolean {
    return depth <= surfaceDepth + (1 - surfaceDepth) * 0.002;
}
