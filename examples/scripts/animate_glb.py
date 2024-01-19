import base64
import io
import json
import pathlib

import numpy as np
import trimesh
from pyquaternion import Quaternion

import ada
from ada.visit.comms import Message, send_to_ws_server
from ada.visit.gltf.gltf_write_animation import Animate


def create_quaternion(axis, degrees):
    # first rotate around x axis, then around y axis
    q = Quaternion(axis=[1, 0, 0], degrees=180)

    q = q * Quaternion(axis=axis, degrees=degrees)
    return q.q


def update_view(scene: trimesh.Scene, tri_anim: Animate = None, look_at=None, camera_position=None,
                new_gltf_file=None, dry_run=False):
    if isinstance(look_at, np.ndarray):
        look_at = look_at.tolist()

    if isinstance(camera_position, np.ndarray):
        camera_position = camera_position.tolist()

    data = io.BytesIO()
    scene.export(file_obj=data, file_type="glb", buffer_postprocessor=tri_anim)
    msg = Message(
        data=base64.b64encode(data.getvalue()).decode(),
        look_at=look_at,
        camera_position=camera_position,
    )
    if dry_run:
        return None

    send_to_ws_server(json.dumps(msg.__dict__))

    # Optionally save binary data to file
    if new_gltf_file is not None:
        data.seek(0)
        new_gltf_file.parent.mkdir(parents=True, exist_ok=True)
        with open(new_gltf_file, "wb") as f:
            f.write(data.read())
    data.close()


def main(new_gltf_file=None):
    if isinstance(new_gltf_file, str):
        new_gltf_file = pathlib.Path(new_gltf_file)

    box = ada.PrimBox('box', (0, 0, 0), (1, 1, 1), color='red')
    a = ada.Assembly('Project') / (ada.Part('part1') / box)

    scene = a.to_trimesh_scene()

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

    tri_anim = Animate("TestAnimation", translation_keyframes, keyframe_times, rotational_keyframes, node_idx=0)

    # Set your initial camera position
    camera_position = p0 + np.array([0.35, 0.25, 0.15])

    # Update the view
    update_view(scene, tri_anim, p0, camera_position, new_gltf_file=new_gltf_file)
    # a.to_ifc("temp/moving_box.ifc")


if __name__ == "__main__":
    main('temp/moving_box.glb')
