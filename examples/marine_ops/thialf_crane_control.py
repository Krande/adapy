"""Load the Thialf crane rig GLB (exported from Blender in the sibling
`marine-ops` repo) and drive it in adapy's browser viewer.

The source rig has no Blender Armature/skinning and no drivers/keyframes to
reverse-engineer real degrees of freedom from -- it's a plain Empty
parent/child hierarchy. The 17 names in JOINTS below are the meaningfully
named empties (crane slew, boom luff, main/aux hoists, vessel move).

`main()` bakes a short smooth rotate-out-and-back glTF animation clip per
joint (three.js interpolates between keyframes -- no instant pose snaps) and
pushes them all to the viewer as one scene. Each joint becomes its own
independently selectable clip in the viewer's existing animation
dropdown/play/pause/scrub controls -- it does not claim the rotation
angle/axis matches real crane kinematics.

Usage:
    python examples/marine_ops/thialf_crane_control.py [--glb PATH] [--angle DEG]

Or interactively (e.g. from IPython), to drive individual joints yourself
without the baked animation:
    from examples.marine_ops.thialf_crane_control import load, set_joint, push, JOINTS
    rm, scene = load()
    set_joint(scene, "Rotation_boom.001", rotation_deg=(0, 0, 30))
    push(rm, scene)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import struct

import numpy as np
import trimesh

from ada.api.animations import Animation
from ada.comms.fb_wrap_model_gen import FilePurposeDC, SceneOperationsDC
from ada.comms.wsock.client_sync import WebSocketClientSync
from ada.visit.render_params import RenderParams
from ada.visit.renderer_manager import RendererManager
from ada.visit.scene_converter import SceneConverter

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


def _build_id_hierarchy(scene: trimesh.Scene) -> dict[str, tuple[str, str]]:
    """Build the `{node_id: (name, parent_id)}` contract the frontend's
    selection/info panel reads from `asset.extras.id_hierarchy` (root parent
    is "*"). Keyed by glTF node index (as a string) so it lines up with the
    ids the frontend already resolves clicks to. Without this, a plain
    trimesh.Scene (not an ada Part/Assembly) has no per-node names for the
    panel to show -- see ada.visit.gltf.graph.GraphStore.to_json_hierarchy,
    whose contract this mirrors.
    """
    node_idx_by_name = _node_index_map(scene)
    parents = scene.graph.transforms.parents

    id_hierarchy: dict[str, tuple[str, str]] = {}
    for name, idx in node_idx_by_name.items():
        parent_name = parents.get(name)
        parent_idx = node_idx_by_name.get(parent_name) if parent_name is not None else None
        parent_id = str(parent_idx) if parent_idx is not None else "*"
        id_hierarchy[str(idx)] = (name, parent_id)
    return id_hierarchy


def load(glb_path: pathlib.Path | str = DEFAULT_GLB) -> tuple[RendererManager, trimesh.Scene]:
    glb_path = pathlib.Path(glb_path).resolve()
    if not glb_path.exists():
        raise FileNotFoundError(
            f"{glb_path} not found. Run `pixi run export-all` in the marine-ops repo first."
        )
    scene = trimesh.load(glb_path, file_type="glb")
    scene.metadata["id_hierarchy"] = _build_id_hierarchy(scene)
    rm = RendererManager(renderer="react")
    return rm, scene


def _parent_of(scene: trimesh.Scene, node: str) -> str:
    return scene.graph.transforms.parents[node]


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


def _node_index_map(scene: trimesh.Scene) -> dict[str, int]:
    """Resolve glTF node name -> index in trimesh's exported node order via a
    dry (no postprocessor) export -- the ordering is fixed by the scene graph
    structure alone, unaffected by buffer/tree postprocessors.
    """
    data = scene.export(file_type="glb")
    chunk_len = struct.unpack("<I", data[12:16])[0]
    tree = json.loads(data[20:20 + chunk_len])
    return {n["name"]: i for i, n in enumerate(tree.get("nodes", [])) if "name" in n}


def _quat_xyzw(matrix: np.ndarray) -> list[float]:
    w, x, y, z = trimesh.transformations.quaternion_from_matrix(matrix)
    return [x, y, z, w]


def build_joint_animations(scene: trimesh.Scene, angle_deg: float = 20.0) -> SceneConverter:
    """Bake a 0 -> peak -> 0 rotation clip per joint into a SceneConverter
    (three.js AnimationMixer slerps between the quaternion keyframes, so
    playback is smooth, not a snap). Returns the converter whose
    buffer_postprocessor/tree_postprocessor must be passed into the same
    `scene.export(...)` call that writes these joints' nodes.
    """
    node_idx_by_name = _node_index_map(scene)
    converter = SceneConverter(None)

    missing = [j for j in JOINTS if j not in node_idx_by_name]
    if missing:
        raise KeyError(f"Joint nodes missing from exported GLB: {missing}")

    for joint in JOINTS:
        node_idx = node_idx_by_name[joint]
        parent = _parent_of(scene, joint)
        base_local, _ = scene.graph.get(frame_to=joint, frame_from=parent)
        peak_local = base_local @ trimesh.transformations.euler_matrix(0, 0, np.radians(angle_deg))

        base_q = _quat_xyzw(base_local)
        peak_q = _quat_xyzw(peak_local)

        converter.add_animation(
            Animation(
                name=joint,
                keyframe_times=[0.0, 1.0, 2.0],
                rotation_keyframes=[base_q, peak_q, base_q],
                node_idx=node_idx,
            )
        )
    return converter


def push_animated(rm: RendererManager, scene: trimesh.Scene, converter: SceneConverter) -> None:
    """Send `scene` with `converter`'s baked animations embedded, bypassing
    RendererManager.render()'s own (animation-less) SceneConverter so the
    baked clips aren't dropped on the way to the viewer.

    Going around SceneConverter.build_scene() this way also means its usual
    `scene.metadata["id_hierarchy"] -> tree["asset"]["extras"]` copy never
    runs, so that's redone here explicitly from whatever `load()` already
    stashed in `scene.metadata` -- otherwise the viewer's selection/info
    panel has no per-node names to show for a plain (non-ada) scene.
    """
    id_hierarchy = scene.metadata.get("id_hierarchy")

    def tree_postprocessor(tree):
        converter.tree_postprocessor(tree)
        if id_hierarchy is not None:
            extras = tree.setdefault("asset", {}).setdefault("extras", {})
            extras["id_hierarchy"] = id_hierarchy

    rm.start_server()
    with WebSocketClientSync(rm.host, rm.ws_port) as wc:
        rm.ensure_liveness(wc)
        wc.update_scene(
            "Thialf",
            scene,
            purpose=FilePurposeDC.DESIGN,
            scene_op=SceneOperationsDC.REPLACE,
            gltf_buffer_postprocessor=converter.buffer_postprocessor,
            gltf_tree_postprocessor=tree_postprocessor,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--glb", type=pathlib.Path, default=DEFAULT_GLB)
    parser.add_argument("--angle", type=float, default=20.0, help="Peak rotation angle in degrees (test angle, not a real DOF)")
    args = parser.parse_args()

    rm, scene = load(args.glb)
    converter = build_joint_animations(scene, angle_deg=args.angle)
    push_animated(rm, scene, converter)
    print(
        f"Sent scene with {len(JOINTS)} baked joint animation clips "
        "(0 -> {:.0f} deg -> 0 over 2s each). Pick one from the viewer's "
        "animation dropdown and press play.".format(args.angle)
    )


if __name__ == "__main__":
    main()
