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
        vertices = None
        normals = None
        indices = None
        id_map = dict()
        for obj in p.get_all_physical_objects():
            geom = obj.solid
            np_vertices, np_faces, np_normals, _ = occ_shape_to_faces(geom, quality, render_edges, parallel)
            if vertices is None:
                vertices = np_vertices
                normals = np_normals
                indices = np_faces
                id_map[obj.guid] = (int(indices[0]), int(indices[-1]))
            else:
                vertices = np.concatenate([vertices, np_vertices])
                normals = np.concatenate([normals, np_normals])
                adjusted_indices = np_faces + len(indices)
                indices = np.concatenate([indices, adjusted_indices])
                id_map[obj.guid] = (int(adjusted_indices[0]), int(adjusted_indices[-1]))

        part_array.append(
            {
                "name": p.name,
                "rawdata": True,
                "guiParam": None,
                "treemeta": {},
                "id_map": id_map,
                "meta": "url til json",
                "vertices": vertices.astype(float).tolist(),
                "normals": normals.astype(float).tolist(),
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
