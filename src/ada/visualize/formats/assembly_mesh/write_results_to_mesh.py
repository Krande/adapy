from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.ifc.utils import create_guid
from ada.visualize.concept import ObjectMesh, PartMesh, VisMesh

if TYPE_CHECKING:
    from ada.fem.results import Results


def export_results_to_assembly_mesh(results: "Results", data_type) -> VisMesh:
    name = results.assembly.name

    res_mesh = results.result_mesh
    data = np.asarray(res_mesh.mesh.point_data[data_type], dtype="float32")
    vertices = np.asarray([x + u[:3] for x, u in zip(res_mesh.vertices, data)], dtype="float32")
    colors = res_mesh.colorize_data(data)
    faces = res_mesh.faces
    guid = create_guid(name)
    id_map = {
        guid: ObjectMesh(
            guid=guid,
            faces=faces.astype(int),
            position=vertices.flatten().astype(float),
            normal=None,
            color=None,
            vertex_color=colors.flatten().astype(float).tolist(),
            instances=None,
        )
    }
    pm = PartMesh(name=name, id_map=id_map)
    project = results.assembly.metadata.get("project", "DummyProject")
    return VisMesh(name=name, project=project, world=[pm], meta=None)
