from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import trimesh
import trimesh.visual
from OCC.Core.Tesselator import ShapeTesselator
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Edge
from OCC.Extend.TopologyUtils import discretize_edge

from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.geom import Geometry
from ada.occ.exceptions import UnableToCreateTesselationFromSolidOCCGeom
from ada.occ.geom import geom_to_occ_geom
from ada.visit.colors import Color
from ada.visit.gltf.meshes import MeshStore, MeshType


@dataclass
class TriangleMesh:
    positions: np.ndarray
    faces: np.ndarray
    edges: np.ndarray = None
    normals: np.ndarray = None


@dataclass
class LineMesh:
    positions: np.ndarray
    indices: np.ndarray
    normals: np.ndarray = None


def tessellate_edges(shape: TopoDS_Edge, deflection=0.01) -> LineMesh:
    points = discretize_edge(shape, deflection=deflection)

    np_edge_vertices = np.array(points, dtype=np.float32)
    np_edge_indices = np.arange(np_edge_vertices.shape[0], dtype=np.uint32)
    return LineMesh(np_edge_vertices, np_edge_indices)


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
    parallel: bool = False
    material_store: dict[Color, int] = field(default_factory=dict)

    def tessellate_occ_geom(self, occ_geom: TopoDS_Shape, geom_id, geom_color) -> MeshStore:
        if isinstance(occ_geom, TopoDS_Edge):
            tess_shape = tessellate_edges(occ_geom)
            indices = tess_shape.indices
            mesh_type = MeshType.LINES
        else:
            tess_shape = tessellate_shape(occ_geom, self.quality, self.render_edges, self.parallel)
            indices = tess_shape.faces
            mesh_type = MeshType.TRIANGLES

        mat_id = self.material_store.get(geom_color, None)
        if mat_id is None:
            mat_id = len(self.material_store)
            self.material_store[geom_color] = mat_id

        return MeshStore(
            geom_id,
            None,
            tess_shape.positions,
            indices,
            tess_shape.normals,
            mat_id,
            mesh_type,
            geom_id,
        )

    def tessellate_geom(self, geom: Geometry) -> MeshStore:
        occ_geom = geom_to_occ_geom(geom)
        return self.tessellate_occ_geom(occ_geom, geom.id, geom.color)

    def batch_tessellate(
            self, objects: Iterable[Geometry | BackendGeom], render_override: dict[str, GeomRepr] = None
    ) -> Iterable[MeshStore]:
        if render_override is None:
            render_override = dict()

        for obj in objects:
            if isinstance(obj, BackendGeom):
                geom_repr = render_override.get(obj.guid, GeomRepr.SOLID)
                if geom_repr == GeomRepr.SOLID:
                    geom = obj.solid_geom()
                elif geom_repr == GeomRepr.SHELL:
                    geom = obj.shell_geom()
                else:
                    geom = obj.line_geom()
            else:
                geom = obj

            yield self.tessellate_geom(geom)

    def get_mat_by_id(self, mat_id: int):
        _data = {value: key for key, value in self.material_store.items()}
        return _data.get(mat_id)
