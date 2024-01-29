from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING, Iterable

import numpy as np
from OCC.Core.Tesselator import ShapeTesselator
from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Shape
from OCC.Extend.TopologyUtils import discretize_edge

from ada.api.spatial import Part
from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.config import logger
from ada.geom import Geometry
from ada.occ.exceptions import UnableToCreateTesselationFromSolidOCCGeom
from ada.occ.geom import geom_to_occ_geom
from ada.visit.colors import Color
from ada.visit.gltf.meshes import MeshStore, MeshType
from ada.visit.gltf.optimize import concatenate_stores
from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

if TYPE_CHECKING:
    import trimesh


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
        tess.Compute(compute_edges=render_edges, mesh_quality=quality, parallel=parallel)
    except RuntimeError as e:
        raise UnableToCreateTesselationFromSolidOCCGeom(f'Failed to tessellate OCC geometry due to "{e}"')

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
    import trimesh.visual

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

    def add_color(self, color: Color) -> int:
        mat_id = self.material_store.get(color, None)
        if mat_id is None:
            mat_id = len(self.material_store)
            self.material_store[color] = mat_id
        return mat_id

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

    def tessellate_geom(self, geom: Geometry, obj: BackendGeom) -> MeshStore:
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCC.Core.gp import gp_Trsf, gp_Vec

        try:
            occ_geom = geom_to_occ_geom(geom)
        except BaseException as e:
            raise UnableToCreateTesselationFromSolidOCCGeom(e)

        if obj is not None and obj.parent is not None and obj.parent.placement is not None:
            position = obj.parent.placement.to_axis2placement3d(use_absolute_placement=True)

            trsf = gp_Trsf()
            trsf.SetTranslation(gp_Vec(*position.location))
            occ_geom = BRepBuilderAPI_Transform(occ_geom, trsf, True).Shape()
            # occ_geom = transform_shape_to_pos(occ_geom, position.location, position.axis, position.ref_direction)

        return self.tessellate_occ_geom(occ_geom, geom.id, geom.color)

    def batch_tessellate(
        self, objects: Iterable[Geometry | BackendGeom], render_override: dict[str, GeomRepr] = None
    ) -> Iterable[MeshStore]:
        if render_override is None:
            render_override = dict()

        for obj in objects:
            if isinstance(obj, BackendGeom):
                ada_obj = obj
                geom_repr = render_override.get(obj.guid, GeomRepr.SOLID)
                if geom_repr == GeomRepr.SOLID:
                    geom = obj.solid_geom()
                elif geom_repr == GeomRepr.SHELL:
                    geom = obj.shell_geom()
                else:
                    geom = obj.line_geom()
            else:
                geom = obj
                ada_obj = None

            # resolve transform based on parent transforms
            try:
                yield self.tessellate_geom(geom, ada_obj)
            except UnableToCreateTesselationFromSolidOCCGeom as e:
                logger.error(e)
                continue

    def tessellate_part(
        self, part: Part, filter_by_guids=None, render_override=None, merge_meshes=True
    ) -> trimesh.Scene:
        import trimesh

        graph = part.get_graph_store()
        scene = trimesh.Scene(base_frame=graph.top_level.name)

        shapes_tess_iter = self.batch_tessellate(
            part.get_all_physical_objects(pipe_to_segments=True, filter_by_guids=filter_by_guids),
            render_override=render_override,
        )

        all_shapes = sorted(shapes_tess_iter, key=lambda x: x.material)
        # filter out all shapes associated with an animation,

        for mat_id, meshes in groupby(all_shapes, lambda x: x.material):
            if merge_meshes:
                merged_store = concatenate_stores(meshes)
                merged_mesh_to_trimesh_scene(scene, merged_store, self.get_mat_by_id(mat_id), mat_id, graph)
            else:
                for mesh_store in meshes:
                    merged_mesh_to_trimesh_scene(scene, mesh_store, self.get_mat_by_id(mat_id), mat_id, graph)

        shell_color = Color.from_str("white")
        shell_color_id = self.add_color(shell_color)
        line_color = Color.from_str("gray")
        line_color_id = self.add_color(line_color)
        points_color = Color.from_str("black")
        points_color_id = self.add_color(points_color)

        for p in part.get_all_subparts(include_self=True):
            if p.fem.is_empty() is True:
                continue

            mesh = p.fem.to_mesh()
            parent_node = graph.nodes.get(p.guid)

            points_store, edge_store, face_store = mesh.create_mesh_stores(
                p.fem.name, shell_color, line_color, points_color, graph, parent_node
            )

            merged_mesh_to_trimesh_scene(scene, face_store, shell_color, shell_color_id, graph)
            merged_mesh_to_trimesh_scene(scene, edge_store, line_color, line_color_id, graph)
            merged_mesh_to_trimesh_scene(scene, points_store, points_color, points_color_id, graph)

        scene.metadata["meta"] = graph.create_meta(suffix="")
        return scene

    def get_mat_by_id(self, mat_id: int):
        _data = {value: key for key, value in self.material_store.items()}
        return _data.get(mat_id)
