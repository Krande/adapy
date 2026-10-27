// Object-mode navigation for a 3D mouse: the model follows the cap. Push the cap
// right and the model moves right; lift it and the model rises; pull it toward
// you and the model comes closer; twist or tilt it and the model turns the same
// way. The camera therefore does the opposite, about the orbit target.
//
// It moves the SAME pose the mouse orbit moves -- the camera position and the
// controls' target -- and nothing else, so a click, a mouse drag, Shift+F or
// Shift+A afterwards carries on from wherever the 3D mouse left the view.
//
// TURNTABLE, NOT FREE ROTATION. Both orbit implementations the viewer runs keep
// the camera level about the world up axis (their update re-aims the camera
// with `lookAt`), so a free rotation would be undone on the next frame and the
// two would fight. Twist turns about world up through the target, tilt pitches
// about the camera's right axis, and pitch stops just short of the poles. Roll
// (tilting the cap sideways) has no level-camera equivalent, so it is off unless
// the user asks for it; then it is an angle the camera is turned by about its own
// view direction (`rolledUp`), applied on top of the level pose rather than mixed
// into it. Twist and tilt stay about world up, and switching roll off returns the
// view to level.
//
// Rates are per second at full deflection, multiplied by the real frame time,
// so the speed is the same at 30 and 144 fps. Pan scales with the distance to
// the target and the field of view, so a full push crosses about the same share
// of the screen at any zoom; zoom is exponential in time, so it feels the same
// close up and far out and can never reach or pass the target.
//
// Allocation-free: the scratch vectors live at module scope, and the pose is
// mutated in place.

import {Quaternion, Vector3} from "three";

export interface NavigationRates {
    /** Radians per second. */
    rotate: number;
    /** View half-heights per second. */
    pan: number;
    /** e-folds of distance per second. */
    zoom: number;
}

export const DEFAULT_RATES: Readonly<NavigationRates> = {rotate: 2.0, pan: 1.5, zoom: 1.5};

/** How close to straight up/down the view may pitch, in radians. */
const POLE_MARGIN = 1e-3;
/** Zoom never brings the camera nearer the target than this. */
const MIN_DISTANCE = 1e-6;

const _forward = new Vector3();
const _right = new Vector3();
const _camUp = new Vector3();
const _offset = new Vector3();
const _delta = new Vector3();
const _panRight = new Vector3();
const _panUp = new Vector3();
const _q = new Quaternion();

/**
 * The camera up vector for a view rolled by `roll` radians about its direction
 * (target - position): `worldUp` turned about that direction, written to `out`.
 * Positive roll turns the picture counter-clockwise on screen, so the model follows
 * a cap rolled counter-clockwise. `lookAt` keeps only the part of `out` across the
 * view direction, which is what makes this a pure roll.
 */
export function rolledUp(position: Vector3, target: Vector3, worldUp: Vector3, roll: number, out: Vector3): Vector3 {
    out.copy(worldUp);
    if (roll === 0) return out;
    _forward.subVectors(target, position);
    if (!(_forward.lengthSq() > 0)) return out;
    _q.setFromAxisAngle(_forward.normalize(), roll);
    return out.applyQuaternion(_q);
}

/**
 * Apply one frame of `axes` (view-frame motion in [-1, 1], tx ty tz rx ry rz)
 * to the pose `position` / `target`, both mutated. `worldUp` must be unit
 * length; `roll` is the view's current roll (`rolledUp`), so a pan follows the
 * screen's axes rather than the level ones. Roll motion itself (`axes[5]`) is
 * not applied here. Returns true when the pose changed.
 */
export function applyObjectModeMotion(
    position: Vector3,
    target: Vector3,
    worldUp: Vector3,
    axes: ArrayLike<number>,
    dt: number,
    fovDeg: number,
    gain: number,
    rates: Readonly<NavigationRates> = DEFAULT_RATES,
    roll = 0,
): boolean {
    if (!(dt > 0) || !(gain > 0)) return false;
    const tx = axes[0], ty = axes[1], tz = axes[2], rx = axes[3], ry = axes[4];
    if (tx === 0 && ty === 0 && tz === 0 && rx === 0 && ry === 0) return false;

    _forward.subVectors(target, position);
    let dist = _forward.length();
    if (!(dist > MIN_DISTANCE)) return false;
    _forward.divideScalar(dist);
    _right.crossVectors(_forward, worldUp);
    if (_right.lengthSq() < 1e-12) {
        // Looking straight along up: any horizontal axis will do as "right".
        _right.set(1, 0, 0).cross(worldUp);
        if (_right.lengthSq() < 1e-12) _right.set(0, 1, 0).cross(worldUp);
    }
    _right.normalize();
    _camUp.crossVectors(_right, _forward);

    // Pan: the model moves with the cap, so the camera and target move against it.
    if (tx !== 0 || ty !== 0) {
        const halfHeight = dist * Math.tan((fovDeg * Math.PI) / 360);
        const s = halfHeight * rates.pan * gain * dt;
        // The screen's axes: the level ones turned by the roll.
        _panRight.copy(_right);
        _panUp.copy(_camUp);
        if (roll !== 0) {
            _q.setFromAxisAngle(_forward, roll);
            _panRight.applyQuaternion(_q);
            _panUp.applyQuaternion(_q);
        }
        _delta.copy(_panRight).multiplyScalar(-tx * s).addScaledVector(_panUp, -ty * s);
        position.add(_delta);
        target.add(_delta);
    }

    // Zoom: pulling the cap toward you brings the model closer.
    if (tz !== 0) {
        const next = dist * Math.exp(-tz * rates.zoom * gain * dt);
        dist = next > MIN_DISTANCE ? next : MIN_DISTANCE;
        position.copy(target).addScaledVector(_forward, -dist);
    }

    if (rx === 0 && ry === 0) return true;
    _offset.subVectors(position, target);

    // Twist: the model turns with the cap about the vertical, so the camera
    // circles the other way.
    if (ry !== 0) {
        _q.setFromAxisAngle(worldUp, -ry * rates.rotate * gain * dt);
        _offset.applyQuaternion(_q);
        _forward.applyQuaternion(_q);
        _right.applyQuaternion(_q);
    }

    // Tilt: rotating the view direction by +a about `right` lifts it, which
    // lowers its polar angle from up by a. Clamp so the polar angle stays inside
    // (POLE_MARGIN, pi - POLE_MARGIN): over the pole the level camera would flip.
    if (rx !== 0) {
        let d = _forward.dot(worldUp);
        d = d > 1 ? 1 : d < -1 ? -1 : d;
        const polar = Math.acos(d);
        let a = -rx * rates.rotate * gain * dt;
        const lo = polar - (Math.PI - POLE_MARGIN);
        const hi = polar - POLE_MARGIN;
        a = a < lo ? lo : a > hi ? hi : a;
        if (a !== 0) {
            _q.setFromAxisAngle(_right, a);
            _offset.applyQuaternion(_q);
        }
    }

    position.copy(target).add(_offset);
    return true;
}
