import numpy as np


def format_color(r, g, b):
    return "#%02x%02x%02x" % (r, g, b)


def get_fem_faces(fem):
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


def get_fem_edges(fem):
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


def get_fem_vertices(fem):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :return:
    """

    return np.asarray([n.p for n in fem.nodes._nodes], dtype="float32")


def magnitude(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
