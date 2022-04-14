from __future__ import annotations

from typing import TYPE_CHECKING

from ada.visualize.concept import PartMesh, VisMesh
from ada.visualize.config import ExportConfig

from .write_objects_to_mesh import obj_to_mesh
from .write_part_to_mesh import generate_meta

if TYPE_CHECKING:
    from ada.concepts.connections import JointBase


def export_joint_to_assembly_mesh(joint: "JointBase", export_config: ExportConfig) -> VisMesh:
    all_obj = [obj for obj in joint.beams]
    all_obj_num = len(all_obj)

    print(f"Exporting {all_obj_num} physical objects to custom json format.")
    obj_num = 1

    id_map = dict()
    for obj in all_obj:
        res = obj_to_mesh(obj, export_config)
        if res is None:
            continue
        id_map[obj.guid] = res
        print(f'Exporting "{obj.name}" ({obj_num} of {all_obj_num})')

    meta = generate_meta(joint.parent, export_config) if joint.parent is not None else None

    return VisMesh(
        name=joint.name,
        project=joint.metadata.get("project", "DummyProject"),
        world=[PartMesh(joint.name, id_map=id_map)],
        meta=meta,
    )
