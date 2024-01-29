
import numpy as np
from pyquaternion import Quaternion

import ada
from ada.api.animations import Animation


def create_quaternion(axis, degrees):
    # first rotate around x axis, then around y axis
    q = Quaternion(axis=[1, 0, 0], degrees=180)

    q = q * Quaternion(axis=axis, degrees=degrees)
    return q.q


def main():
    box = ada.PrimBox("box", (0, 0, 0), (1, 1, 1), color="red")
    a = ada.Assembly("Project") / (ada.Part("part1") / box)

    # Define Animation
    w = 0.04
    p0 = np.array([-0.04, 0.010, 1.86])
    p1 = p0 + np.array([0.0, 0.0, w])
    p2 = p1 + np.array([-0.9, 0.00, 0])
    p3 = p2 + np.array([0, 0.0, -w])
    p4 = p3 + np.array([0.9, 0.00, 0])

    translation_keyframes = [p0, p1, p2, p3, p4]  # Example translations
    # Add a rotation that turns 90 degrees around the z axis per keyframe
    rotational_keyframes = [
        create_quaternion((0, 1, 0), 0),
        create_quaternion((0, 1, 0), 90),
        create_quaternion((0, 1, 0), 180),
        create_quaternion((0, 1, 0), 270),
        create_quaternion((0, 1, 0), 360),
    ]
    keyframe_times = [0, 1, 3, 4, 6]  # Example keyframe times

    tri_anim = Animation("TestAnimation", box, translation_keyframes, keyframe_times, rotational_keyframes)
    a.animation_store.add(tri_anim)

    # Set your initial camera position
    camera_position = p0 + np.array([0.35, 0.25, 0.15])

    # Update the view
    a.show(renderer="react", camera_position=camera_position.astype(float).tolist())
    # a.to_ifc("temp/moving_box.ifc")


if __name__ == "__main__":
    main()
