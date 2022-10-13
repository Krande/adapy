from __future__ import annotations

from dataclasses import dataclass, field

import meshio
import numpy as np

from ada.fem.formats.general import FEATypes


@dataclass
class FEAResultSet:
    name: str
    step: int
    components: list[str]
    values: list[tuple] = field(repr=False)


@dataclass
class FEAResult:
    name: str
    software: str | FEATypes
    results: list[FEAResultSet]
    mesh: meshio.Mesh
    element_ids: np.ndarray = None
    point_ids: np.ndarray = None

    def to_gltf(self):
        from ada.visualize.femviz import get_edges_and_faces_from_meshio

        _ = np.asarray(self.mesh.points, dtype="float32")

        edges, faces = get_edges_and_faces_from_meshio(self.mesh)
        _ = np.asarray(edges, dtype="uint16").ravel()
        _ = np.asarray(faces, dtype="uint16").ravel()
