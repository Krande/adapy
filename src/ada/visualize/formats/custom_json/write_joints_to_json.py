from __future__ import annotations

from typing import TYPE_CHECKING

from .config import ExportConfig
from .write_objects_to_json import obj_to_json

if TYPE_CHECKING:
    from ada.concepts.connections import JointBase


def export_joint_to_json(joint: "JointBase", export_config: ExportConfig) -> dict:
    all_obj = [obj for obj in joint.beams]
    all_obj_num = len(all_obj)

    print(f"Exporting {all_obj_num} physical objects to custom json format.")
    obj_num = 1

    id_map = dict()
    for obj in all_obj:
        res = obj_to_json(obj, export_config)
        if res is None:
            continue
        id_map[obj.guid] = res.to_dict()
        print(f'Exporting "{obj.name}" ({obj_num} of {all_obj_num})')

    output = {
        "name": joint.name,
        "created": "dato",
        "project": joint.metadata.get("project", "DummyProject"),
        "world": [id_map],
    }
    return output
