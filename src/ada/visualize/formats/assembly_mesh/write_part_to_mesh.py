from __future__ import annotations

from typing import TYPE_CHECKING, Union

import ada
from ada.visualize.concept import PartMesh, VisMesh

from .config import ExportConfig

if TYPE_CHECKING:
    from ada import Part


def export_part_to_assembly_mesh(part: "Part", export_config: ExportConfig) -> Union[None, VisMesh]:
    all_obj_num = len(list(part.get_all_physical_objects()))
    print(f"Exporting {all_obj_num} physical objects to custom json format.")

    obj_num = 1
    part_array = []
    for p in [part, *part.get_all_subparts()]:
        pjson = part_to_part_mesh(p, export_config, obj_num, all_obj_num)
        part_array.append(pjson)

    return VisMesh(
        name=part.name,
        project=part.metadata.get("project", "DummyProject"),
        world=part_array,
        meta=generate_meta(part, export_config),
    )


def part_to_part_mesh(p: "Part", export_config: ExportConfig, obj_num, all_obj_num) -> PartMesh:
    from .write_objects_to_mesh import (
        id_map_using_threading,
        list_of_obj_to_object_mesh_map,
    )

    if export_config.threads != 1:
        id_map = id_map_using_threading(list(p.get_all_physical_objects()), export_config.threads)
    else:
        id_map = list_of_obj_to_object_mesh_map(p.get_all_physical_objects(), obj_num, all_obj_num, export_config)

    for inst in p.instances.values():
        id_map[inst.instance_ref.guid].instances = inst.to_list_of_custom_json_matrices()

    return PartMesh(name=p.name, id_map=id_map)


def generate_meta(part: ada.Part, export_config: ExportConfig):
    meta = dict()
    for obj in part.get_all_physical_objects(
        sub_elements_only=False,
        filter_by_guids=export_config.data_filter.filter_elements_by_guid,
    ):
        meta[obj.guid] = (obj.name, obj.parent.guid)
        if export_config.data_filter.name_filter is not None and len(export_config.data_filter.name_filter) > 0:
            if obj.name not in [fi.lower() for fi in export_config.data_filter.name_filter]:
                continue

    for p in part.get_all_parts_in_assembly(True):
        parent_id = p.parent.guid if p.parent is not None else None
        if isinstance(p.parent, ada.Assembly):
            parent_id = "*"
        meta[p.guid] = (p.name, parent_id)

    return meta
