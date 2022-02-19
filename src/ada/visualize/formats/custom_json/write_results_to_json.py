from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.ifc.utils import create_guid

if TYPE_CHECKING:
    from ada.fem.results import Results


def export_results_to_json(results: "Results", data_type) -> dict:
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
    return output
