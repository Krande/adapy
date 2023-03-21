from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterable

import numpy as np

from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.config import get_logger
from ada.core.utils import thread_this
from ada.occ.exceptions.geom_creation import (
    UnableToBuildNSidedWires,
    UnableToCreateSolidOCCGeom,
    UnableToCreateTesselationFromSolidOCCGeom,
)
from ada.visualize.concept import ObjectMesh
from ada.visualize.config import ExportConfig
from ada.visualize.renderer_occ import occ_shape_to_faces

if TYPE_CHECKING:
    from ada import Beam, PipeSegElbow, PipeSegStraight, Plate, Shape, Wall

logger = get_logger()


def filter_mesh_objects(objects: Iterable[BackendGeom], export_config: ExportConfig) -> None | list[BackendGeom]:
    from ada import Pipe

    guid_filter = export_config.data_filter.filter_elements_by_guid
    obj_list: list[BackendGeom] = []

    for obj in objects:
        if guid_filter is not None and obj.guid not in guid_filter:
            continue
        if isinstance(obj, Pipe):
            for seg in obj.segments:
                obj_list.append(seg)
        else:
            obj_list.append(obj)

    if len(obj_list) == 0:
        return None

    return obj_list


def ifc_poly_elem_to_json(
    obj: Shape, export_config: ExportConfig = ExportConfig(), opt_func: Callable = None
) -> list[ObjectMesh]:
    import ifcopenshell.geom

    from ada.ifc.utils import create_guid, get_representation_items
    from ada.visualize.utils import merge_mesh_objects, organize_by_colour

    a = obj.get_assembly()
    ifc_store = a.ifc_store
    ifc_f = ifc_store.f
    ifc_elem = ifc_f.by_guid(obj.guid)

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_PYTHON_OPENCASCADE, False)
    settings.set(settings.SEW_SHELLS, False)
    settings.set(settings.WELD_VERTICES, True)
    settings.set(settings.INCLUDE_CURVES, False)
    settings.set(settings.USE_WORLD_COORDS, True)
    settings.set(settings.VALIDATE_QUANTITIES, False)

    geom_items = get_representation_items(ifc_f, ifc_elem)
    meshes = []
    if len(geom_items) == 0:
        raise ValueError("No IFC geometries found.")

    for i, ifc_geom in enumerate(geom_items):
        geometry = ifc_store.get_ifc_geom(ifc_geom, settings)
        guid = obj.guid if i == 0 else create_guid()
        vertices = np.array(geometry.verts, dtype="float32").reshape(int(len(geometry.verts) / 3), 3)
        faces = np.array(geometry.faces, dtype=int)
        normals = np.array(geometry.normals) if len(geometry.normals) != 0 else None

        if normals is not None and len(normals) > 0:
            normals = normals.astype(dtype="float32").reshape(int(len(normals) / 3), 3)

        if opt_func is not None:
            faces, vertices, normals = opt_func(faces.reshape(int(len(geometry.faces) / 3), 3), vertices, normals)
            vertices = vertices.astype(dtype="float32").flatten()
            faces = faces.astype(dtype="int32").flatten()
            if normals is not None:
                normals = normals.astype(dtype="float32").flatten()

        mats = geometry.materials
        if len(mats) == 0:
            colour = [1.0, 0.0, 0.0, 1.0]
        else:
            mat0 = mats[0]
            opacity = 1.0 - mat0.transparency
            colour = [*mat0.diffuse, opacity]

        obj_mesh = ObjectMesh(guid, faces, vertices, normals, colour, translation=export_config.volume_center)
        meshes.append(obj_mesh)

    if export_config.merge_subgeometries_by_colour is False or len(meshes) == 1:
        return meshes

    # merge subgeometries by colour
    colour_map = organize_by_colour(meshes)
    merged_meshes = []
    main_obj = False
    applied_guid = False
    for i, (colour, elements) in enumerate(colour_map.items()):
        obj_mesh = merge_mesh_objects(elements)

        if len(obj_mesh.faces) == 0:
            continue

        if colour[-1] == 1.0 and applied_guid is False:
            main_obj = True
            obj_mesh.guid = obj.guid
            applied_guid = True

        merged_meshes.append(obj_mesh)

    if main_obj is False:
        merged_meshes[0].guid = obj.guid

    return merged_meshes


def occ_geom_to_poly_mesh(
    obj: BackendGeom,
    export_config: ExportConfig = ExportConfig(),
    opt_func: Callable = None,
    geom_repr: GeomRepr = GeomRepr.SOLID,
) -> ObjectMesh:
    if geom_repr == GeomRepr.SOLID:
        geom = obj.solid()
    elif geom_repr == GeomRepr.SHELL:
        geom = obj.shell()
    else:
        export_config.render_edges = True
        geom = obj.line()

    position, indices, normals, _ = occ_shape_to_faces(
        geom,
        export_config.quality,
        export_config.render_edges,
        export_config.parallel,
    )

    if opt_func is not None:
        indices, position, normals = opt_func(indices, position, normals)
    else:
        opt_func_example(indices, position, normals)

    colour = [*obj.colour_norm, obj.opacity]
    return ObjectMesh(obj.guid, indices, position, normals, colour, translation=export_config.volume_center)


def obj_to_mesh(
    obj: Beam | Plate | Wall | PipeSegElbow | PipeSegStraight | Shape,
    export_config: ExportConfig = ExportConfig(),
    opt_func: Callable = None,
) -> None | list[ObjectMesh]:
    if obj.parent.get_assembly().ifc_store is not None and export_config.ifc_skip_occ is True:
        try:
            obj_meshes = ifc_poly_elem_to_json(obj, export_config, opt_func)
        except RuntimeError as e:
            logger.error(e)
            return None
    else:
        try:
            obj_meshes = occ_geom_to_poly_mesh(obj, export_config, opt_func)
        except (UnableToBuildNSidedWires, UnableToCreateTesselationFromSolidOCCGeom, UnableToCreateSolidOCCGeom) as e:
            logger.error(e)
            return None

    return obj_meshes


def id_map_using_threading(list_in, threads: int):
    # obj = list_in[0]
    # obj_str = json.dumps(obj)
    # serialize_evaluator(obj)
    res = thread_this(list_in, obj_to_mesh, threads)
    print(res)
    return res


def opt_func_example(faces, position, normals):
    """Optimize by finding removing vertices with same coordinates and normals"""
    obj_buffer_arrays = np.concatenate([position, normals], 1)
    buffer, indices = np.unique(obj_buffer_arrays, axis=0, return_index=False, return_inverse=True)
    x, y, z, nx, ny, nz = buffer.T
    position = np.array([x, y, z]).T
    normals = np.array([nx, ny, nz]).T
    return faces, position, normals
