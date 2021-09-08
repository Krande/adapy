import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Union

import numpy as np
from OCC.Core.Tesselator import ShapeTesselator
from OCC.Core.TopoDS import TopoDS_Shape
from pythreejs import (
    BufferAttribute,
    BufferGeometry,
    LineMaterial,
    LineSegments2,
    LineSegmentsGeometry,
    Mesh,
)

from ada.concepts.piping import Pipe
from ada.concepts.primitives import Shape
from ada.concepts.structural import Beam, Plate, Wall
from ada.fem.shapes import ElemType

from .threejs_utils import create_material


class NORMAL(Enum):
    SERVER_SIDE = 1
    CLIENT_SIDE = 2


@dataclass
class VizObj:
    obj: Union[Beam, Plate, Wall, Shape, Pipe]
    geom_repr: str = ElemType.SOLID
    edge_color: tuple = None
    mesh: Mesh = None
    edges: LineSegments2 = None

    def get_geom(self, geom_repr):
        if geom_repr == ElemType.SOLID:
            return self.obj.solid
        elif geom_repr == ElemType.SHELL:
            return self.obj.shell
        elif geom_repr == ElemType.LINE:
            return self.obj.line
        else:
            raise ValueError(f'Unrecognized "{geom_repr}".')

    def occ_to_verts_and_faces(self, parallel=True, render_edges=True, quality=1.0):
        geom = self.get_geom(self.geom_repr)
        np_vertices, np_faces, np_normals, edges = occ_shape_to_faces(geom, quality, render_edges, parallel)
        return np_vertices, np_faces, np_normals, edges

    def convert_to_pythreejs_mesh(self):
        o = OccToThreejs()
        self.mesh, self.edges = o.occ_shape_to_threejs(
            self.obj.solid, self.obj.colour, self.edge_color, self.obj.transparent, self.obj.opacity
        )

    def convert_to_ipygany_mesh(self):
        from ipygany import PolyMesh

        np_vertices, np_faces, np_normals, edges = self.occ_to_verts_and_faces()
        return PolyMesh(vertices=np_vertices, triangle_indices=np_faces)


@dataclass
class OccToThreejs:
    parallel = True
    compute_normals_mode = NORMAL.SERVER_SIDE
    render_edges = True
    quality = 1.0
    mesh_id: str = None

    def occ_shape_to_threejs(self, shp: TopoDS_Shape, shape_color, edge_color, transparency, opacity):
        # first, compute the tesselation
        np_vertices, np_faces, np_normals, edges = occ_shape_to_faces(
            shp, self.quality, self.render_edges, self.parallel
        )

        # set geometry properties
        buffer_geometry_properties = {
            "position": BufferAttribute(np_vertices),
            "index": BufferAttribute(np_faces),
        }
        if self.compute_normals_mode == NORMAL.SERVER_SIDE:
            if np_normals.shape != np_vertices.shape:
                raise AssertionError("Wrong number of normals/shapes")
            buffer_geometry_properties["normal"] = BufferAttribute(np_normals)

        # build a BufferGeometry instance
        shape_geometry = BufferGeometry(attributes=buffer_geometry_properties)

        # if the client has to render normals, add the related js instructions
        if self.compute_normals_mode == NORMAL.CLIENT_SIDE:
            shape_geometry.exec_three_obj_method("computeVertexNormals")

        # then a default material
        shp_material = create_material(shape_color, transparent=transparency, opacity=opacity)

        # and to the dict of shapes, to have a mapping between meshes and shapes
        mesh_id = "%s" % uuid.uuid4().hex

        self.mesh_id = mesh_id
        # finally create the mesh
        shape_mesh = Mesh(geometry=shape_geometry, material=shp_material, name=mesh_id)

        # edge rendering, if set to True
        if self.render_edges:
            edge_list = flatten(list(map(explode, edges)))
            lines = LineSegmentsGeometry(positions=edge_list)
            mat = LineMaterial(linewidth=1, color=edge_color)
            edge_lines = LineSegments2(lines, mat, name=mesh_id)
        else:
            edge_lines = None

        return shape_mesh, edge_lines


def explode(edge_list):
    return [[edge_list[i], edge_list[i + 1]] for i in range(len(edge_list) - 1)]


def flatten(nested_dict):
    return [y for x in nested_dict for y in x]


def occ_shape_to_faces(shape, quality=1.0, render_edges=False, parallel=True):
    """

    :param shape:
    :param quality:
    :param render_edges:
    :param parallel:
    :return:
    """
    # first, compute the tesselation
    tess = ShapeTesselator(shape)
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
    np_vertices = np.array(vertices_position, dtype="float32").reshape(int(number_of_vertices / 3), 3)
    # Note: np_faces is just [0, 1, 2, 3, 4, 5, ...], thus arange is used
    np_faces = np.arange(np_vertices.shape[0], dtype="uint32")

    np_normals = np.array(tess.GetNormalsAsTuple(), dtype="float32").reshape(-1, 3)
    edges = list(
        map(
            lambda i_edge: [tess.GetEdgeVertex(i_edge, i_vert) for i_vert in range(tess.ObjEdgeGetVertexCount(i_edge))],
            range(tess.ObjGetEdgeCount()),
        )
    )
    return np_vertices, np_faces, np_normals, edges
