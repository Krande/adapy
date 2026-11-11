// The one roll rule both viewports use: the world's up turned about the view direction, right-handed,
// exactly as a quaternion about that direction turns it.
import {test} from "node:test";
import assert from "node:assert/strict";
import {Quaternion, Vector3} from "three";
import {rolledUpAxis} from "../../utils/camera/roll";

const views: Array<[[number, number, number], [number, number, number], [number, number, number]]> = [
    [[10, 0, 0], [0, 0, 0], [0, 0, 1]],
    [[0, -10, 3], [1, 2, 0], [0, 0, 1]],
    [[5, 5, 5], [0, 0, 0], [0, 1, 0]],
    [[-3, 8, -2], [4, -1, 1], [0, 0, 1]],
];

for (const [eye, target, up] of views) {
    for (const roll of [0.3, -0.7, 1.5, Math.PI - 0.1]) {
        test(`roll ${roll} from ${eye} to ${target} about up ${up}`, () => {
            const axis = new Vector3(...target).sub(new Vector3(...eye)).normalize();
            const want = new Vector3(...up).applyQuaternion(new Quaternion().setFromAxisAngle(axis, roll));
            const got = rolledUpAxis(eye, target, up, roll);
            for (let i = 0; i < 3; i++) assert.ok(Math.abs(got[i] - want.getComponent(i)) < 1e-12, `${got} vs ${want.toArray()}`);
        });
    }
}

test("no roll, or no view direction, leaves the world's up", () => {
    assert.deepEqual(rolledUpAxis([1, 2, 3], [0, 0, 0], [0, 0, 1], 0), [0, 0, 1]);
    assert.deepEqual(rolledUpAxis([1, 2, 3], [1, 2, 3], [0, 0, 1], 0.5), [0, 0, 1]);
});
