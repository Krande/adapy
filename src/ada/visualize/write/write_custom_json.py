import json
import os
import pathlib
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ada import Assembly


def make_obj_verts_normals(geom, render_edges, quality, parallel):
    from OCC.Core.Tesselator import ShapeTesselator

    tess = ShapeTesselator(geom)
    tess.Compute(compute_edges=render_edges, mesh_quality=quality, parallel=parallel)
    vertices_position = tess.GetVerticesPositionAsTuple()
    number_of_vertices = len(vertices_position)
    vert_2 = int(number_of_vertices / 3)

    obj_verts = np.array(tess.GetVerticesPositionAsTuple(), dtype="float32").reshape(vert_2, 3)
    obj_normals = np.array(tess.GetNormalsAsTuple(), dtype="float32").reshape(vert_2, 3)
    return obj_verts, obj_normals


def make_obj_buffer(obj_verts, obj_normals):
    obj_buffer = np.concatenate([obj_verts, obj_normals], 1)
    obj_buffer_flat = obj_buffer.flatten()
    unique = np.unique(obj_buffer_flat)
    obj_indices = np.searchsorted(unique, obj_buffer_flat)
    return obj_buffer_flat, obj_indices


def to_custom_json(assembly: "Assembly", output_file_path):
    quality = 1.0
    render_edges = False
    parallel = True

    part_array = []

    for p in assembly.parts.values():
        obj_indices_map = dict()
        id_map = dict()
        prev_index = 0
        indices_raw_data = None
        for obj in p.get_all_physical_objects():
            geom = obj.solid
            obj_verts, obj_normals = make_obj_verts_normals(geom, render_edges, quality, parallel)
            indices_input_data = np.concatenate([obj_verts, obj_normals], 1).flatten()

            obj_indices_map[obj] = dict(data=indices_input_data)
            curr_index = prev_index + len(indices_input_data)
            if prev_index != 0:
                curr_index -= 1
            id_map[obj.guid] = dict(indexGroup=(prev_index, curr_index), color=[*obj.colour_norm, obj.opacity])
            prev_index += curr_index + 1
            if indices_raw_data is None:
                indices_raw_data = indices_input_data
            else:
                indices_raw_data = np.concatenate([indices_raw_data, indices_input_data])

        buffer = np.unique(indices_raw_data)
        indices = np.searchsorted(buffer, indices_raw_data)
        part_array.append(
            {
                "name": p.name,
                "rawdata": True,
                "guiParam": None,
                "treemeta": {},
                "id_map": id_map,
                "meta": "url til json",
                "buffer": buffer.astype(float).tolist(),
                "index": indices.astype(int).tolist(),
            }
        )
    output = {
        "name": assembly.name,
        "created": "dato",
        "project": assembly.metadata.get("project", "DummyProject"),
        "world": part_array,
    }
    output_file_path = pathlib.Path(output_file_path)
    os.makedirs(output_file_path.parent, exist_ok=True)
    with open(output_file_path, "w") as f:
        json.dump(output, f, indent=4)
