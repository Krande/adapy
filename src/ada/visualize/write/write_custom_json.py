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
        for obj in p.get_all_physical_objects():
            geom = obj.solid
            np_vertices, poly_indices, np_normals, _ = occ_shape_to_faces(geom, quality, render_edges, parallel)
            obj_buffer_arrays = np.concatenate([np_vertices, np_normals], 1)
            curr_index = prev_index + len(poly_indices)
            if prev_index != 0:
                curr_index -= 1

            buffer, indices = np.unique(obj_buffer_arrays, axis=0, return_index=False, return_inverse=True)
            id_map[obj.guid] = dict(
                matrix=None,
                index=indices.astype(int).tolist(),
                buffer=buffer.flatten().astype(float).tolist(),
                color=[*obj.colour_norm, obj.opacity],
            )
        instance_map = dict()
        for inst in p.instances.values():
            instance_map[inst.instance_ref] = inst.to_list_of_json_matrices()

        part_array.append(
            {
                "name": p.name,
                "instances": instance_map,
                "rawdata": True,
                "guiParam": None,
                "treemeta": {},
                "id_map": id_map,
                "meta": "url til json"
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
