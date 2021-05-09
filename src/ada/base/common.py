import numpy as np


def format_color(r, g, b):
    return "#%02x%02x%02x" % (r, g, b)


def get_edges_and_faces_from_mesh(mesh):
    """

    :param mesh:
    :type mesh: meshio.Mesh
    :return:
    """
    from ada.fem import ElemShapes
    from ada.fem.io.io_meshio import meshio_to_ada_type

    edges = []
    faces = []
    for cell_block in mesh.cells:
        el_type = meshio_to_ada_type[cell_block.type]
        for elem in cell_block.data:
            res = ElemShapes(el_type, elem)
            edges += res.edges
            faces += res.faces
    return edges, faces


def get_faces_from_fem(fem):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :return:
    :rtype: list
    """
    ids = []
    for el in fem.elements.elements:
        for f in el.shape.faces:
            # Convert to indices, not id
            ids += [[int(e.id - 1) for e in f]]
    return ids


def get_edges_from_fem(fem):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :return:
    :rtype: list
    """
    ids = []
    for el in fem.elements.elements:
        for f in el.shape.edges_seq:
            # Convert to indices, not id
            ids += [[int(el.nodes[e].id - 1) for e in f]]
    return ids


def get_vertices_from_fem(fem):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :return:
    """

    return np.asarray([n.p for n in fem.nodes._nodes], dtype="float32")


def get_bounding_box(vertices):
    return np.min(vertices, 0), np.max(vertices, 0)


def magnitude(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
