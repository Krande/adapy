from __future__ import annotations

import json
import logging
import os
import pathlib
from typing import TYPE_CHECKING, Union

import numpy as np

from ada.core.utils import thread_this
from ada.ifc.utils import create_guid
from ada.occ.exceptions.geom_creation import (
    UnableToBuildNSidedWires,
    UnableToCreateSolidOCCGeom,
    UnableToCreateTesselationFromSolidOCCGeom,
)

from ..renderer_occ import occ_shape_to_faces

if TYPE_CHECKING:
    from ada import (
        Assembly,
        Beam,
        Part,
        PipeSegElbow,
        PipeSegStraight,
        Plate,
        Shape,
        Wall,
    )
    from ada.fem.results import Results


def to_custom_json(ada_obj: Union["Assembly", "Part", "Results"], output_file_path, threads: int = 1, data_type=None):
    from ada import Part
    from ada.fem.results import Results

    if issubclass(type(ada_obj), Part):
        export_assembly_to_json(ada_obj, output_file_path, threads)
    elif isinstance(ada_obj, Results):
        if data_type is None:
            raise ValueError('Please pass in a "data_type" value in order to export results mesh')
        export_results_to_json(ada_obj, output_file_path, data_type)
    else:
        NotImplementedError(f'Currently not supporting export of type "{type(ada_obj)}"')


def export_assembly_to_json(part: "Part", output_file_path, threads: int = 1):
    all_obj = [obj for p in part.parts.values() for obj in p.get_all_physical_objects()]

    all_obj += list(part.get_all_physical_objects())

    all_obj_num = len(all_obj)

    print(f"Exporting {all_obj_num} physical objects to custom json format.")
    obj_num = 1

    part_array = [part_to_json_values(part, threads, obj_num, all_obj_num)]
    for p in part.parts.values():
        pjson = part_to_json_values(p, threads, obj_num, all_obj_num)
        part_array.append(pjson)

    output = {
        "name": part.name,
        "created": "dato",
        "project": part.metadata.get("project", "DummyProject"),
        "world": part_array,
    }
    output_file_path = pathlib.Path(output_file_path)
    os.makedirs(output_file_path.parent, exist_ok=True)
    with open(output_file_path, "w") as f:
        json.dump(output, f, indent=4)


def export_results_to_json(results: "Results", output_file_path, data_type):
    res_mesh = results.result_mesh

    data = np.asarray(res_mesh.mesh.point_data[data_type], dtype="float32")
    vertices = np.asarray([x + u[:3] for x, u in zip(res_mesh.vertices, data)], dtype="float32")
    colors = res_mesh.colorize_data(data)
    faces = res_mesh.faces

    id_map = {
        create_guid(): dict(
            index=faces.astype(int).tolist(),
            wireframeGeometry=True,
            position=vertices.flatten().astype(float).tolist(),
            normal=None,
            color=None,
            vertexColor=colors.flatten().astype(float).tolist(),
            instances=None,
        )
    }

    part_array = [
        {
            "name": "Results",
            "rawdata": True,
            "guiParam": None,
            "treemeta": {},
            "id_map": id_map,
            "meta": "url til json",
        }
    ]

    output = {
        "name": results.assembly.name,
        "created": "dato",
        "project": results.assembly.metadata.get("project", "DummyProject"),
        "world": part_array,
    }
    output_file_path = pathlib.Path(output_file_path)
    os.makedirs(output_file_path.parent, exist_ok=True)
    with open(output_file_path, "w") as f:
        json.dump(output, f, indent=4)


def convert_obj_to_poly(obj, quality=1.0, render_edges=False, parallel=False):
    geom = obj.solid
    np_vertices, poly_indices, np_normals, _ = occ_shape_to_faces(geom, quality, render_edges, parallel)
    obj_buffer_arrays = np.concatenate([np_vertices, np_normals], 1)
    buffer, indices = np.unique(obj_buffer_arrays, axis=0, return_index=False, return_inverse=True)
    return dict(
        guid=obj.guid,
        index=indices.astype(int).tolist(),
        position=buffer.flatten().astype(float).tolist(),
        color=[*obj.colour_norm, obj.opacity],
        instances=[],
    )


def part_to_json_values(p: "Part", threads, obj_num, all_obj_num) -> dict:
    from ada import Pipe

    if threads != 1:
        id_map = id_map_using_threading(list(p.get_all_physical_objects()), threads)
    else:
        id_map = dict()
        for obj in p.get_all_physical_objects():
            obj_num += 1
            if isinstance(obj, Pipe):
                for seg in obj.segments:
                    res = obj_to_json(seg)
                    if res is None:
                        continue
                    id_map[seg.guid] = res
                    print(f'Exporting "{obj.name}" ({obj_num} of {all_obj_num})')
            else:
                res = obj_to_json(obj)
                if res is None:
                    continue
                id_map[obj.guid] = res
                print(f'Exporting "{obj.name}" ({obj_num} of {all_obj_num})')

    for inst in p.instances.values():
        id_map[inst.instance_ref.guid]["instances"] = inst.to_list_of_custom_json_matrices()

    return {
        "name": p.name,
        "rawdata": True,
        "guiParam": None,
        "treemeta": {},
        "id_map": id_map,
        "meta": "url til json",
    }


def serialize_evaluator(obj):
    def serialize_printer(obj_):
        try:
            for key, val in obj_.__dict__.items():
                if type(val) in [type(None), type(float), type(str), type(list)]:
                    continue
                if "parent" in key.lower() or "__" in key:
                    continue
                print(key, type(val))
                if "swig" in str(type(val)).lower():
                    print(key, val)
                    raise ValueError()
                serialize_printer(val)
        except AttributeError:
            return

    serialize_printer(obj)


def id_map_using_threading(list_in, threads: int):
    # obj = list_in[0]
    # obj_str = json.dumps(obj)
    # serialize_evaluator(obj)
    res = thread_this(list_in, obj_to_json, threads)
    print(res)
    return res


def obj_to_json(obj: Union[Beam, Plate, Wall, PipeSegElbow, PipeSegStraight, Shape]) -> Union[dict, None]:
    quality = 1.0
    render_edges = False
    parallel = True
    try:
        geom = obj.solid
    except UnableToCreateSolidOCCGeom as e:
        logging.error(e)
        return None
    except UnableToBuildNSidedWires as e:
        logging.error(e)
        return None
    try:
        obj_position, poly_indices, normals, _ = occ_shape_to_faces(geom, quality, render_edges, parallel)
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


def bump_version(name, url, version_file, refresh_ver_file=False):
    version_file = pathlib.Path(version_file)
    os.makedirs(version_file.parent, exist_ok=True)
    if version_file.exists() is False:
        data = dict()
    else:
        with open(version_file, "r") as f:
            data = json.load(f)
    if refresh_ver_file:
        data = dict()

    if name not in data.keys():
        data[name] = dict(url=url, version=0)
    else:
        obj = data[name]
        obj["url"] = url
        obj["version"] += 1

    with open(version_file, "w") as f:
        json.dump(data, f, indent=4)