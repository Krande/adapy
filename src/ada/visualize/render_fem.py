import numpy as np
from IPython.display import display
from ipywidgets import HBox, VBox

from .fem import get_faces_from_fem, magnitude
from .renderer import MyRenderer
from .threejs_geom import faces_to_mesh


def render_mesh(vertices, faces, colors):
    """
    Renders

    :param vertices:
    :param faces:
    :param colors:
    :return:
    """

    mesh = faces_to_mesh("faces", vertices, faces, colors)

    renderer = MyRenderer()
    renderer._displayed_pickable_objects.add(mesh)
    renderer.build_display()
    display(HBox([VBox([HBox(renderer._controls), renderer._renderer]), renderer.html]))


def viz_meshio_mesh(fem, mesh, data_type):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :param mesh:
    :type mesh: meshio.
    :param data_type:
    :type data_type:
    :return:
    :rtype:
    """
    u = np.asarray(mesh.point_data[data_type], dtype="float32")
    vertices = np.asarray(mesh.points, dtype="float32")
    faces = np.asarray(get_faces_from_fem(fem), dtype="uint16").ravel()

    res = [magnitude(u_) for u_ in u]
    max_r = max(res)
    res_norm_col = np.asarray([(x / max_r, 0, 0) for x in res], dtype="float32")

    render_mesh(vertices, faces, res_norm_col)
