import numpy as np


def occ_shape_to_faces(shape, quality=1.0, render_edges=False, parallel=True):
    from OCC.Core.Tesselator import ShapeTesselator

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
