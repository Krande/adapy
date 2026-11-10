// The orientation gizmo's picture, as plain numbers: where each world axis' two ends are on a small
// disc for a camera, back to front. Drawn by NativeOrientationGizmo; no three.js.
export type Vec3 = readonly [number, number, number];

export interface AxisEnd {
    /** "X", "-X", "Y", ... */
    label: string;
    /** The world direction it stands for, which is also the direction to look from when it is clicked. */
    direction: [number, number, number];
    /** On the disc: x right, y down, each -1 .. 1. */
    x: number;
    y: number;
    /** Towards the camera when positive. */
    depth: number;
    positive: boolean;
    axis: 0 | 1 | 2;
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/** The six axis ends for a camera whose screen axes are `right` and `up` and which looks along -`back`,
 *  sorted back to front so the nearer ones are drawn over the others. */
export function axisEnds(right: Vec3, up: Vec3, back: Vec3): AxisEnd[] {
    const ends: AxisEnd[] = [];
    for (const axis of [0, 1, 2] as const) {
        for (const sign of [1, -1]) {
            const direction: [number, number, number] = [0, 0, 0];
            direction[axis] = sign;
            ends.push({
                label: (sign < 0 ? "-" : "") + "XYZ"[axis],
                direction,
                x: dot(direction, right),
                y: -dot(direction, up),
                depth: dot(direction, back),
                positive: sign > 0,
                axis,
            });
        }
    }
    return ends.sort((a, b) => a.depth - b.depth);
}
