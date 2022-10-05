from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Callable

import ifcopenshell.geom
import numpy as np

from ada.ifc.utils import create_guid
from ada.visualize.concept import ObjectMesh, PartMesh, VisMesh
from ada.visualize.config import ExportConfig
from ada.visualize.formats.assembly_mesh.write_objects_to_mesh import (
    filter_mesh_objects,
    obj_to_mesh,
)
from ada.visualize.formats.assembly_mesh.write_part_to_mesh import generate_meta
from ada.visualize.utils import from_cache

if TYPE_CHECKING:
    from ada import Part


def part_to_vis_mesh(
    part: Part,
    auto_sync_ifc_store=True,
    export_config: ExportConfig = ExportConfig(),
    opt_func: Callable = None,
    auto_merge_by_color=True,
    overwrite_cache=False,
):
    if auto_sync_ifc_store:
        part.get_assembly().ifc_store.sync()

    if export_config is None:
        export_config = ExportConfig()

    all_obj_num = len(list(part.get_all_physical_objects(sub_elements_only=False)))
    print(f"Exporting {all_obj_num} physical objects to custom json format.")

    obj_num = 1
    subgeometries = dict()
    part_array = []
    for p in part.get_all_subparts(include_self=True):
        if export_config.max_convert_objects is not None and obj_num > export_config.max_convert_objects:
            break
        obj_list = filter_mesh_objects(p.get_all_physical_objects(sub_elements_only=True), export_config)
        if obj_list is None:
            continue
        id_map = dict()
        for obj in obj_list:
            print(f'Exporting "{obj.name}" [{obj.get_assembly().name}] ({obj_num} of {all_obj_num})')
            cache_file = pathlib.Path(f".cache/{part.name}.h5")
            if export_config.use_cache is True and cache_file.exists():
                res = from_cache(cache_file, obj.guid)
                if res is None:
                    res = obj_to_mesh(obj, export_config, opt_func=opt_func)
            else:
                res = obj_to_mesh(obj, export_config, opt_func=opt_func)
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
        name=part.name,
        project=part.metadata.get("project", "DummyProject"),
        world=part_array,
        meta=generate_meta(part, export_config, sub_geometries=subgeometries),
    )
    if export_config.use_cache:
        amesh.to_cache(overwrite_cache)

    if auto_merge_by_color:
        return amesh.merge_objects_in_parts_by_color()

    return amesh


def part_to_vis_mesh2(part: Part, auto_sync_ifc_store=True):
    ifc_store = part.get_assembly().ifc_store
    if auto_sync_ifc_store:
        ifc_store.sync()

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_PYTHON_OPENCASCADE, False)
    settings.set(settings.SEW_SHELLS, False)
    settings.set(settings.WELD_VERTICES, True)
    settings.set(settings.INCLUDE_CURVES, False)
    settings.set(settings.USE_WORLD_COORDS, True)
    settings.set(settings.VALIDATE_QUANTITIES, False)

    iterator = ifc_store.get_ifc_geom_iterator(settings)
    iterator.initialize()
    id_map = dict()

    while True:
        shape = iterator.get()
        if shape:
            obj_mesh = product_to_obj_mesh(shape)
            id_map[shape.guid] = obj_mesh

        if not iterator.next():
            break

    part = PartMesh(name=part.name, id_map=id_map)

    return VisMesh(part.name, world=[part])


def product_to_obj_mesh(shape: ifcopenshell.ifcopenshell_wrapper.TriangulationElement) -> ObjectMesh:
    geometry = shape.geometry
    vertices = np.array(geometry.verts, dtype="float32").reshape(int(len(geometry.verts) / 3), 3)
    faces = np.array(geometry.faces, dtype=int)
    normals = np.array(geometry.normals) if len(geometry.normals) != 0 else None

    if normals is not None and len(normals) > 0:
        normals = normals.astype(dtype="float32").reshape(int(len(normals) / 3), 3)

    mats = geometry.materials
    if len(mats) == 0:
        colour = [1.0, 0.0, 0.0, 1.0]
    else:
        mat0 = mats[0]
        opacity = 1.0 - mat0.transparency
        colour = [*mat0.diffuse, opacity]

    return ObjectMesh(shape.guid, faces, vertices, normals, colour)
