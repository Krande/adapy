from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import trimesh
import trimesh.visual
from OCC.Core.Tesselator import ShapeTesselator
from OCC.Core.TopoDS import TopoDS_Shape

from ada.geom import Geometry
from ada.geom.solids import Box
from ada.occ.exceptions import UnableToCreateTesselationFromSolidOCCGeom
from ada.occ.geom import make_box_from_geom
from ada.visit.colors import Color
from ada.visit.gltf.meshes import MeshStore, MeshType


@dataclass
class TriangleMesh:
    positions: np.ndarray
    faces: np.ndarray
    edges: np.ndarray = None
    normals: np.ndarray = None


def tessellate_shape(shape: TopoDS_Shape, quality=1.0, render_edges=False, parallel=True) -> TriangleMesh:
    # first, compute the tesselation
    try:
        tess = ShapeTesselator(shape)
    except RuntimeError as e:
        raise UnableToCreateTesselationFromSolidOCCGeom(e)

    tess.Compute(compute_edges=render_edges, mesh_quality=quality, parallel=parallel)

    # get vertices and normals
    vertices_position = tess.GetVerticesPositionAsTuple()
    number_of_triangles = tess.ObjGetTriangleCount()
    number_of_vertices = len(vertices_position)

    # number of vertices should be a multiple of 3
    if number_of_vertices % 3 != 0:
        raise AssertionError("Wrong number of vertices")
    if number_of_triangles * 9 != number_of_vertices:
        raise AssertionError("Wrong number of triangles")

    # then we build the vertex and faces collections as numpy ndarrays
    np_vertices = np.array(vertices_position, dtype="float32")
    np_faces = np.arange(number_of_triangles * 3, dtype="uint32")
    np_normals = np.array(tess.GetNormalsAsTuple(), dtype="float32")
    edges = np.array(
        map(
            lambda i_edge: [tess.GetEdgeVertex(i_edge, i_vert) for i_vert in range(tess.ObjEdgeGetVertexCount(i_edge))],
            range(tess.ObjGetEdgeCount()),
        )
    )

    return TriangleMesh(np_vertices, np_faces, edges, np_normals)


def shape_to_tri_mesh(shape: TopoDS_Shape, rgba_color: Iterable[float, float, float, float] = None) -> trimesh.Trimesh:
    tm = tessellate_shape(shape)
    positions = tm.positions.reshape(len(tm.positions) // 3, 3)
    faces = tm.faces.reshape(len(tm.faces) // 3, 3)
    mesh = trimesh.Trimesh(vertices=positions, faces=faces)
    mesh.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(baseColorFactor=rgba_color)
    )
    return mesh


@dataclass
class BatchTessellator:
    quality: float = 1.0
    render_edges: bool = False
    parallel: bool = True
    material_store: dict[Color, int] = field(default_factory=dict)

    def tessellate_geom(self, geom: Geometry) -> MeshStore:
        if isinstance(geom.geometry, Box):
            occ_geom = make_box_from_geom(geom.geometry)
        else:
            raise NotImplementedError()

        tess_shape = tessellate_shape(occ_geom, self.quality, self.render_edges, self.parallel)
        mat_id = self.material_store.get(geom.color, None)
        if mat_id is None:
            mat_id = len(self.material_store)
            self.material_store[geom.color] = mat_id

        return MeshStore(geom.id, None, tess_shape.positions, tess_shape.faces, tess_shape.normals, mat_id,
                         MeshType.TRIANGLES, geom.id)

    def batch_tessellate(self, geoms: Iterable[Geometry]) -> Iterable[MeshStore]:
        for geom in sorted(geoms, key=lambda x: x.color):
            yield self.tessellate_geom(geom)
