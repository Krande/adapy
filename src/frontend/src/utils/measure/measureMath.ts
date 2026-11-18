// The Measure tab's arithmetic, on plain numbers: distances and their components, angles, a path's
// length, and how each is written. No three.js, no store.
//
// Units: a glTF file's unit of length is the metre (glTF 2.0 says so, and adapy writes its GLBs in
// metres), and the viewer neither scales nor converts a model, so a length in the world is a length
// in metres. Lengths are written to the millimetre, or a tenth of one under a metre; angles in degrees
// to a hundredth.

export type Vec3 = readonly [number, number, number];

export const sub = (a: Vec3, b: Vec3): [number, number, number] => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const add = (a: Vec3, b: Vec3): [number, number, number] => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const scale = (a: Vec3, s: number): [number, number, number] => [a[0] * s, a[1] * s, a[2] * s];
export const dot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a: Vec3, b: Vec3): [number, number, number] => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const norm = (a: Vec3): number => Math.hypot(a[0], a[1], a[2]);
export const lerp = (a: Vec3, b: Vec3, t: number): [number, number, number] => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

export function distance(a: Vec3, b: Vec3): number {
    return norm(sub(b, a));
}

/** From `a` to `b`: the length and its components along the axes (signed, b - a). */
export function components(a: Vec3, b: Vec3): {length: number; dx: number; dy: number; dz: number} {
    const [dx, dy, dz] = sub(b, a);
    return {length: Math.hypot(dx, dy, dz), dx, dy, dz};
}

const DEG = 180 / Math.PI;

/** The angle between two directions, 0..180 degrees; null when either has no length. */
export function angleBetween(u: Vec3, v: Vec3): number | null {
    const lu = norm(u), lv = norm(v);
    if (lu === 0 || lv === 0) return null;
    // atan2 of |u x v| and u . v: exact near 0 and 180, where acos of the dot is not.
    return Math.atan2(norm(cross(u, v)), dot(u, v)) * DEG;
}

/** The angle at `vertex` between the arms to `a` and to `b`, 0..180 degrees; null when an arm has no
 *  length. */
export function angleAt(a: Vec3, vertex: Vec3, b: Vec3): number | null {
    return angleBetween(sub(a, vertex), sub(b, vertex));
}

/** The angle between two lines (edges have no direction), 0..90 degrees; null for one of no length. */
export function angleBetweenLines(u: Vec3, v: Vec3): number | null {
    const a = angleBetween(u, v);
    return a === null ? null : Math.min(a, 180 - a);
}

/** The lengths of a path's legs and their sum. */
export function pathLength(points: readonly Vec3[]): {segments: number[]; total: number} {
    const segments: number[] = [];
    for (let i = 1; i < points.length; i++) segments.push(distance(points[i - 1], points[i]));
    return {segments, total: segments.reduce((s, v) => s + v, 0)};
}

/** The arc that shows the angle at `vertex` from the arm to `a` to the arm to `b`: `steps` segments
 *  (six numbers each) at `radius` from the vertex. Empty when the angle is not defined. */
export function arcSegments(a: Vec3, vertex: Vec3, b: Vec3, radius: number, steps = 24): Float32Array {
    const u = sub(a, vertex), v = sub(b, vertex);
    const lu = norm(u);
    const angle = angleBetween(u, v);
    if (angle === null || lu === 0 || angle === 0) return new Float32Array(0);
    // An orthonormal pair in the plane of the arms: e1 along the first arm, e2 towards the second.
    const e1 = scale(u, 1 / lu);
    let w = sub(v, scale(e1, dot(v, e1)));
    if (norm(w) < 1e-12) {
        // Straight (180 degrees): any direction square to the arm will do.
        w = Math.abs(e1[0]) < 0.9 ? cross(e1, [1, 0, 0]) : cross(e1, [0, 1, 0]);
    }
    const e2 = scale(w, 1 / norm(w));
    const theta = angle / DEG;
    const out = new Float32Array(steps * 6);
    const at = (t: number): [number, number, number] => add(vertex, add(scale(e1, radius * Math.cos(t)), scale(e2, radius * Math.sin(t))));
    for (let i = 0; i < steps; i++) {
        const p = at((theta * i) / steps), q = at((theta * (i + 1)) / steps);
        out.set([...p, ...q], i * 6);
    }
    return out;
}

/** The point halfway along the arc `arcSegments` draws: where the angle's label goes. */
export function arcMiddle(a: Vec3, vertex: Vec3, b: Vec3, radius: number): [number, number, number] {
    const arc = arcSegments(a, vertex, b, radius, 2);
    return arc.length ? [arc[3], arc[4], arc[5]] : [vertex[0], vertex[1], vertex[2]];
}

// ----- how values are written -----------------------------------------------------------------------

export const LENGTH_UNIT = "m";

/** A length in metres: to the millimetre, to a tenth of one under a metre. */
export function formatLength(metres: number): string {
    const digits = Math.abs(metres) < 1 ? 4 : 3;
    return `${fixed(metres, digits)} ${LENGTH_UNIT}`;
}

/** A signed component of a length, as `formatLength` writes it with its sign. */
export function formatSigned(metres: number): string {
    const text = formatLength(metres);
    return metres > 0 ? `+${text}` : text;
}

export function formatAngle(degrees: number): string {
    return `${fixed(degrees, 2)}°`;
}

/** A coordinate triple: each to the millimetre, in metres. */
export function formatPoint(p: Vec3): string {
    return `${fixed(p[0], 3)}, ${fixed(p[1], 3)}, ${fixed(p[2], 3)}`;
}

/** toFixed without "-0.000". */
function fixed(v: number, digits: number): string {
    const text = v.toFixed(digits);
    return /^-0\.?0*$/.test(text) ? text.slice(1) : text;
}
