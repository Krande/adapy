from typing import TYPE_CHECKING

import numpy as np

from ada.visualize.renderer_occ import occ_shape_to_faces

if TYPE_CHECKING:
    from ada import Assembly


def to_gltf(assembly: "Assembly", output_file_path):
    quality = 1.0
    render_edges = False
    parallel = True

    for p in assembly.parts.values():
        vertices = None
        normals = None
        indices = None
        id_map = dict()
        for obj in p.get_all_physical_objects():
            geom = obj.solid()
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

    raise NotImplementedError("Export to GLTF is not yet supported")
