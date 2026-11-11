// Where the camera stands for each named view, as plain numbers: shared by every viewport. No three.js.
import {type CameraView} from "@/layout/coreCommandIds";

/** Looking straight down or up, the up axis is parallel to the view and the picture's roll is
 *  undefined: the camera is set this far off the axis, toward the front for top (so +Y, the back,
 *  is up on the screen) and toward the back for bottom. */
const POLE_NUDGE = 1e-3;

const ISO = 1 / Math.sqrt(3);

/** Where the camera stands from the target, per view, in a Z-up world (unit length). */
const Z_UP: Readonly<Record<CameraView, readonly [number, number, number]>> = {
    top: [0, -POLE_NUDGE, 1],
    bottom: [0, POLE_NUDGE, -1],
    front: [0, -1, 0],
    back: [0, 1, 0],
    left: [-1, 0, 0],
    right: [1, 0, 0],
    // From the front, right and above.
    isometric: [ISO, -ISO, ISO],
};

/** The unit vector from the target to the camera for `view`, in a Z-up (default) or Y-up world. */
export function namedViewDirection(view: CameraView, zIsUp = true): [number, number, number] {
    const [x, y, z] = Z_UP[view];
    const v: [number, number, number] = zIsUp ? [x, y, z] : [x, z, -y];
    const n = Math.hypot(v[0], v[1], v[2]);
    return [v[0] / n, v[1] / n, v[2] / n];
}
