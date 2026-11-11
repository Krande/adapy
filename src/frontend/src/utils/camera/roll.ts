// A view rolled about its direction, as plain numbers: the camera's up axis is the world's up turned by
// `roll` radians about the unit view direction (target - eye), right-handed. Positive roll turns the
// picture counter-clockwise on screen. The one rule for every viewport; no three.js.

export type Vec3 = [number, number, number];

/** The rolled up axis, written to `out`. The world's up when there is no roll or no view direction. */
export function rolledUpAxis(
    eye: ArrayLike<number>,
    target: ArrayLike<number>,
    worldUp: ArrayLike<number>,
    roll: number,
    out: Vec3 = [0, 0, 0],
): Vec3 {
    const vx = worldUp[0], vy = worldUp[1], vz = worldUp[2];
    out[0] = vx;
    out[1] = vy;
    out[2] = vz;
    if (roll === 0) return out;
    const dx = target[0] - eye[0], dy = target[1] - eye[1], dz = target[2] - eye[2];
    const l = Math.hypot(dx, dy, dz);
    if (!(l > 0)) return out;
    const kx = dx / l, ky = dy / l, kz = dz / l;
    const c = Math.cos(roll), s = Math.sin(roll);
    const dot = (kx * vx + ky * vy + kz * vz) * (1 - c);
    // Rodrigues: v c + (k x v) s + k (k . v)(1 - c).
    out[0] = vx * c + (ky * vz - kz * vy) * s + kx * dot;
    out[1] = vy * c + (kz * vx - kx * vz) * s + ky * dot;
    out[2] = vz * c + (kx * vy - ky * vx) * s + kz * dot;
    return out;
}
