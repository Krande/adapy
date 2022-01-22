import json
import os
import pathlib
from typing import TYPE_CHECKING

import numpy as np

from ..renderer_occ import occ_shape_to_faces

if TYPE_CHECKING:
    from ada import Assembly


def to_custom_json(assembly: "Assembly", output_file_path):
    quality = 1.0
    render_edges = False
    parallel = True

    part_array = []

    for p in assembly.parts.values():
        id_map = dict()
        prev_index = 0
        buffer_arrays = None
        for obj in p.get_all_physical_objects():
            geom = obj.solid
            np_vertices, poly_indices, np_normals, _ = occ_shape_to_faces(geom, quality, render_edges, parallel)
            obj_buffer_arrays = np.concatenate([np_vertices, np_normals], 1)
            curr_index = prev_index + len(poly_indices)
            if prev_index != 0:
                curr_index -= 1
            id_map[obj.guid] = dict(
                indexGroup=(prev_index, curr_index),
                color=[*obj.colour_norm, obj.opacity],
            )
            prev_index += curr_index + 1
            if buffer_arrays is None:
                buffer_arrays = obj_buffer_arrays
            else:
                buffer_arrays = np.concatenate([buffer_arrays, obj_buffer_arrays])

        unique_rows, indices = np.unique(buffer_arrays, axis=0, return_index=False, return_inverse=True)
        part_array.append(
            {
                "name": p.name,
                "rawdata": True,
                "guiParam": None,
                "treemeta": {},
                "id_map": id_map,
                "meta": "url til json",
                "buffer": unique_rows.flatten().astype(float).tolist(),
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
