import json
import os
import pathlib
from typing import TYPE_CHECKING, Union

import numpy as np

from ada.core.utils import thread_this

from ..renderer_occ import occ_shape_to_faces

if TYPE_CHECKING:
    from ada import Assembly
    from ada.fem.results import Results


def to_custom_json(ada_obj: Union["Assembly", "Results"], output_file_path, threads: int = 1):
    from ada import Assembly
    from ada.fem.results import Results

    if isinstance(ada_obj, Assembly):
        export_assembly_to_json(ada_obj, output_file_path, threads)
    elif isinstance(ada_obj, Results):
        export_results_to_json(ada_obj, output_file_path)
    else:
        NotImplementedError(f'Currently not supporting export of type "{type(ada_obj)}"')


def export_assembly_to_json(assembly: "Assembly", output_file_path, threads: int = 1):
    part_array = []
    quality = 1.0
    render_edges = False
    parallel = True

    for p in assembly.parts.values():
        if threads != 1:
            id_map = id_map_using_threading(p.get_all_physical_objects(), threads)
        else:
            id_map = dict()
            for obj in p.get_all_physical_objects():
                geom = obj.solid
                np_vertices, poly_indices, np_normals, _ = occ_shape_to_faces(geom, quality, render_edges, parallel)
                obj_buffer_arrays = np.concatenate([np_vertices, np_normals], 1)
                buffer, indices = np.unique(obj_buffer_arrays, axis=0, return_index=False, return_inverse=True)
                id_map[obj.guid] = dict(
                    index=indices.astype(int).tolist(),
                    buffer=buffer.flatten().astype(float).tolist(),
                    color=[*obj.colour_norm, obj.opacity],
                    instances=[],
                )
                print(f'adding "{obj.name}"')

        for inst in p.instances.values():
            id_map[inst.instance_ref.guid]["instances"] = inst.to_list_of_custom_json_matrices()

        part_array.append(
            {
                "name": p.name,
                "rawdata": True,
                "guiParam": None,
                "treemeta": {},
                "id_map": id_map,
                "meta": "url til json",
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


def export_results_to_json(results: "Results", output_file_path):
    results.result_mesh
    raise NotImplementedError()


def convert_obj_to_poly(obj, quality=1.0, render_edges=False, parallel=False):
    geom = obj.solid
    np_vertices, poly_indices, np_normals, _ = occ_shape_to_faces(geom, quality, render_edges, parallel)
    obj_buffer_arrays = np.concatenate([np_vertices, np_normals], 1)
    buffer, indices = np.unique(obj_buffer_arrays, axis=0, return_index=False, return_inverse=True)
    return dict(
        guid=obj.guid,
        index=indices.astype(int).tolist(),
        buffer=buffer.flatten().astype(float).tolist(),
        color=[*obj.colour_norm, obj.opacity],
        instances=[],
    )


def id_map_using_threading(list_in, threads: int):
    res = thread_this(list_in, convert_obj_to_poly, threads)
    print(res)
    return res
