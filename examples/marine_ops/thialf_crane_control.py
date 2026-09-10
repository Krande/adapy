"""Load the Thialf crane rig GLB (exported from Blender in the sibling
`marine-ops` repo) and drive it live in adapy's browser viewer.

The source rig has no Blender Armature/skinning and no drivers/keyframes to
reverse-engineer real degrees of freedom from -- it's a plain Empty
parent/child hierarchy. The 17 names in JOINTS below are the meaningfully
named empties (crane slew, boom luff, main/aux hoists, vessel move); the demo
below rotates each one in turn by an arbitrary test angle so you can see,
visually, which part of the model each joint controls. It does not claim
those angles/axes match real crane kinematics.

Usage:
    python examples/marine_ops/thialf_crane_control.py [--glb PATH]

Or interactively (e.g. from IPython), to drive it yourself:
    from examples.marine_ops.thialf_crane_control import load, set_joint, push, JOINTS
    rm, scene = load()
    set_joint(scene, "Rotation_boom.001", rotation_deg=(0, 30, 0))
    push(rm, scene)
"""
from __future__ import annotations

import argparse
import pathlib
import time

import numpy as np
import trimesh

from ada.visit.render_params import RenderParams
from ada.visit.renderer_manager import RendererManager

DEFAULT_GLB = pathlib.Path(__file__).parents[2] / ".." / "marine-ops" / "files" / "2026_08_17_Thialf_main_skeleton.glb"

JOINTS = [
    "Empty_crane_rotation_SB",
    "Empty_crane_rotation_PS",
    "Rotation_boom.001",
    "Rotation_boom.002",
    "Main_hoist_SB.001",
    "Main_hoist_SB.002",
    "Main_hoist_SB.004",
    "Main_hoist_SB.005",
    "Aux_hoist_nr1_SB.001",
    "Aux_hoist_nr1_SB.002",
    "Aux_hoist_nr1_SB.004",
    "Aux_hoist_nr1_SB.005",
    "Aux_hoist_nr2_SB.001",
    "Aux_hoist_nr2_SB.002",
    "Aux_hoist_nr2_SB.004",
    "Aux_hoist_nr2_SB.005",
    "empty_move_thialf",
]


def load(glb_path: pathlib.Path | str = DEFAULT_GLB) -> tuple[RendererManager, trimesh.Scene]:
    glb_path = pathlib.Path(glb_path).resolve()
    if not glb_path.exists():
        raise FileNotFoundError(
            f"{glb_path} not found. Run `pixi run export-all` in the marine-ops repo first."
        )
    scene = trimesh.load(glb_path, file_type="glb")
    rm = RendererManager(renderer="react")
    return rm, scene


def _parent_of(scene: trimesh.Scene, node: str) -> str:
    for parent, child in scene.graph.transforms.edge_data.keys():
        if child == node:
            return parent
    raise KeyError(f"No parent found for node {node!r} (is it a root node?)")


def set_joint(
    scene: trimesh.Scene,
    node: str,
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> None:
    """Overwrite `node`'s parent-relative transform: base transform (as loaded)
    composed with the given delta rotation (XYZ euler, degrees) and translation.
    """
    if node not in scene.graph.nodes:
        raise KeyError(f"Unknown node {node!r}")

    parent = _parent_of(scene, node)
    base_local, _ = scene.graph.get(frame_to=node, frame_from=parent)

    delta = trimesh.transformations.euler_matrix(*np.radians(rotation_deg))
    delta[:3, 3] = translation

    new_local = base_local @ delta
    scene.graph.update(frame_to=node, frame_from=parent, matrix=new_local)


def push(rm: RendererManager, scene: trimesh.Scene) -> None:
    rm.render(scene, RenderParams())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--glb", type=pathlib.Path, default=DEFAULT_GLB)
    parser.add_argument("--angle", type=float, default=20.0, help="Test rotation angle in degrees")
    parser.add_argument("--pause", type=float, default=1.5, help="Seconds between joint moves")
    args = parser.parse_args()

    rm, scene = load(args.glb)
    push(rm, scene)  # opens the browser viewer, sends the initial (untouched) scene
    time.sleep(2)  # give the viewer a moment to connect before the first update

    for joint in JOINTS:
        print(f"Rotating {joint} by {args.angle} deg about Z (test angle, not real DOF axis)")
        set_joint(scene, joint, rotation_deg=(0, 0, args.angle))
        push(rm, scene)
        time.sleep(args.pause)
        set_joint(scene, joint, rotation_deg=(0, 0, 0))  # reset before moving to the next joint
        push(rm, scene)


if __name__ == "__main__":
    main()
