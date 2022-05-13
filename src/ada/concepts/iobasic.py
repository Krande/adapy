from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from ada.ifc.utils import create_guid
from ada.visualize.concept import PartMesh, VisMesh
from ada.visualize.config import ExportConfig
from ada.visualize.formats.assembly_mesh.write_objects_to_mesh import (
    filter_mesh_objects,
    obj_to_mesh,
)
from ada.visualize.formats.assembly_mesh.write_part_to_mesh import generate_meta
from ada.visualize.utils import from_cache

if TYPE_CHECKING:
    from ada import Part


def vm_from_part(part_obj: Part, export_config=None):

    if export_config is None:
        export_config = ExportConfig()

    all_obj_num = len(list(part_obj.get_all_physical_objects(sub_elements_only=False)))
    print(f"Exporting {all_obj_num} physical objects to custom json format.")

    obj_num = 1
    subgeometries = dict()
    part_array = []
    for p in part_obj.get_all_subparts(include_self=True):
        if export_config.max_convert_objects is not None and obj_num > export_config.max_convert_objects:
            break
        obj_list = filter_mesh_objects(p.get_all_physical_objects(sub_elements_only=True), export_config)
        if obj_list is None:
            continue
        id_map = dict()
        for obj in obj_list:
            print(f'Exporting "{obj.name}" [{obj.get_assembly().name}] ({obj_num} of {all_obj_num})')
            cache_file = pathlib.Path(f".cache/{part_obj.name}.h5")
            if export_config.use_cache is True and cache_file.exists():
                res = from_cache(cache_file, obj.guid)
                if res is None:
                    res = obj_to_mesh(obj, export_config)
            else:
                res = obj_to_mesh(obj, export_config)
            if res is None:
                continue
            if type(res) is list:
                for i, obj_mesh in enumerate(res):
                    if i > 0:
                        name = f"{obj.name}_{i}"
                        guid = create_guid(export_config.name_prefix + name)
                        obj_mesh.guid = guid
                        subgeometries[obj_mesh.guid] = (name, obj.parent.guid)
                    id_map[obj_mesh.guid] = obj_mesh
            else:
                id_map[obj.guid] = res
            obj_num += 1
            if export_config.max_convert_objects is not None and obj_num >= export_config.max_convert_objects:
                print(f'Maximum number of converted objects of "{export_config.max_convert_objects}" reached')
                break

        if id_map is None:
            print(f'Part "{p.name}" has no physical members. Skipping.')
            continue

        for inst in p.instances.values():
            id_map[inst.instance_ref.guid].instances = inst.to_list_of_custom_json_matrices()

        part_array.append(PartMesh(name=p.name, id_map=id_map))

    amesh = VisMesh(
        name=part_obj.name,
        project=part_obj.metadata.get("project", "DummyProject"),
        world=part_array,
        meta=generate_meta(part_obj, export_config, sub_geometries=subgeometries),
    )
    if export_config.use_cache:
        amesh.to_cache(overwrite_cache)

    if auto_merge_by_color:
        return amesh.merge_objects_in_parts_by_color()

    return amesh


def flatten_obj_list(part_obj: Part, export_config: ExportConfig):
    obj_num = 1
    obj_list = []
    for p in part_obj.get_all_subparts(include_self=True):
        if export_config.max_convert_objects is not None and obj_num > export_config.max_convert_objects:
            break
        obj_list += filter_mesh_objects(p.get_all_physical_objects(sub_elements_only=True), export_config)
        if obj_list is None:
            continue

    return obj_list
