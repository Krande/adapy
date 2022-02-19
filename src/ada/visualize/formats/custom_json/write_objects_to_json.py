from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable, Union

import numpy as np

from ada.core.utils import thread_this
from ada.occ.exceptions.geom_creation import (
    UnableToBuildNSidedWires,
    UnableToCreateSolidOCCGeom,
    UnableToCreateTesselationFromSolidOCCGeom,
)
from ada.visualize.renderer_occ import occ_shape_to_faces

from .config import ExportConfig

if TYPE_CHECKING:
    from ada import Beam, PipeSegElbow, PipeSegStraight, Plate, Shape, Wall


def list_of_obj_to_json(
    list_of_all_objects: Iterable[Union[Beam, Plate, Wall, PipeSegElbow, PipeSegStraight, Shape]],
    obj_num: int,
    all_obj_num: int,
    export_config: ExportConfig,
):
    from ada import Pipe

    id_map = dict()
    for obj in list_of_all_objects:
        obj_num += 1
        if isinstance(obj, Pipe):
            for seg in obj.segments:
                res = obj_to_json(seg, export_config)
                if res is None:
                    continue
                id_map[seg.guid] = res
                print(f'Exporting "{obj.name}" ({obj_num} of {all_obj_num})')
        else:
            res = obj_to_json(obj, export_config)
            if res is None:
                continue
            id_map[obj.guid] = res
            print(f'Exporting "{obj.name}" ({obj_num} of {all_obj_num})')

    return id_map


def obj_to_json(
    obj: Union[Beam, Plate, Wall, PipeSegElbow, PipeSegStraight, Shape], export_config: ExportConfig = ExportConfig()
) -> Union[dict, None]:
    render_edges = False
    try:
        geom = obj.solid
    except UnableToCreateSolidOCCGeom as e:
        logging.error(e)
        return None
    except UnableToBuildNSidedWires as e:
        logging.error(e)
        return None
    try:
        obj_position, poly_indices, normals, _ = occ_shape_to_faces(
            geom, export_config.quality, render_edges, export_config.parallel
        )
    except UnableToCreateTesselationFromSolidOCCGeom as e:
        logging.error(e)
        return None

    obj_buffer_arrays = np.concatenate([obj_position, normals], 1)
    buffer, indices = np.unique(obj_buffer_arrays, axis=0, return_index=False, return_inverse=True)
    x, y, z, nx, ny, nz = buffer.T
    position = np.array([x, y, z]).T
    normals = np.array([nx, ny, nz]).T

    return dict(
        index=indices.astype(int).tolist(),
        position=position.flatten().astype(float).tolist(),
        normal=normals.flatten().astype(float).tolist(),
        color=[*obj.colour_norm, obj.opacity],
        vertexColor=None,
        instances=None,
    )


def id_map_using_threading(list_in, threads: int):
    # obj = list_in[0]
    # obj_str = json.dumps(obj)
    # serialize_evaluator(obj)
    res = thread_this(list_in, obj_to_json, threads)
    print(res)
    return res
