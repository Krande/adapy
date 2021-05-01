import pathlib

import numpy as np
from IPython.display import display
from ipywidgets import HBox, VBox
from pythreejs import BufferAttribute, BufferGeometry, Mesh, MeshBasicMaterial

from .renderer import MyRenderer


def make_geom(vertices, faces, colors):
    """

    :param vertices:
    :param faces:
    :param colors:
    :return:
    """
    geometry = BufferGeometry(
        attributes=dict(
            position=BufferAttribute(vertices, normalized=False),
            index=BufferAttribute(faces, normalized=False),
            color=BufferAttribute(colors),
        )
    )
    material = MeshBasicMaterial(vertexColors="VertexColors", side="DoubleSide")
    mesh = Mesh(
        geometry=geometry,
        material=material,
        # position=[-0.5, -0.5, -0.5],  # Center the cube
    )
    return mesh


def get_mesh_faces(fem):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :return:
    :rtype: list
    """
    faceids = []
    for el in fem.elements.elements:
        for f in el.shape.faces:
            # Convert to indices, not id
            faceids += [[e.id - 1 for e in f]]
    return faceids


def render_mesh(vertices, faces, colors):
    """
    Renders

    :param vertices:
    :param faces:
    :param colors:
    :return:
    """

    mesh = make_geom(vertices, faces, colors)

    renderer = MyRenderer()
    renderer._displayed_pickable_objects.add(mesh)
    renderer.build_display()
    display(HBox([VBox([HBox(renderer._controls), renderer._renderer]), renderer.html]))


def magnitude(u_):
    return np.sqrt(u_[0] ** 2 + u_[1] ** 2 + u_[2] ** 2)


def viz_fem(fem, mesh, data_type):
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
    faces = np.asarray(get_mesh_faces(fem), dtype="uint16").ravel()

    res = [magnitude(u_) for u_ in u]
    max_r = max(res)
    res_norm_col = np.asarray([(x / max_r, 0, 0) for x in res], dtype="float32")

    render_mesh(vertices, faces, res_norm_col)


class Results:
    def __init__(self, part, result_file):
        self.palette = [(0, 0, 0), (1, 0, 0)]
        self._part = part
        self._analysis_type = None
        self._point_data = []
        self._cell_data = []
        self._read_result_file(result_file)
        self._renderer = None

    def _get_mesh(self, file_ref):
        import meshio

        file_ref = pathlib.Path(file_ref)
        if file_ref.suffix.lower() == ".rmed":
            mesh = meshio.read(file_ref, "med")
            self._analysis_type = "code_aster"
        else:
            mesh = meshio.read(file_ref)

        return mesh

    def _read_result_file(self, file_ref):
        mesh = self._get_mesh(file_ref)
        self._mesh = mesh
        self._vertices = np.asarray(mesh.points, dtype="float32")
        self._faces = np.asarray(get_mesh_faces(self._part.fem), dtype="uint16").ravel()

        for n in mesh.point_data.keys():
            self._point_data.append(n)

        for n in mesh.cell_data.keys():
            self._cell_data.append(n)

    def _colorize_data(self, data):
        res = [magnitude(d) for d in data]
        max_r = max(res)
        colors = np.asarray([(x / max_r, 0, 0) for x in res], dtype="float32")
        return colors

    def _viz_geom(self, data_type, displ_data=False):
        mesh = self._mesh

        data = np.asarray(mesh.point_data[data_type], dtype="float32")

        # deformations
        if displ_data:
            vertices = np.asarray([x + u[:3] for x, u in zip(self._vertices, data)], dtype="float32")
        else:
            vertices = self._vertices

        # Colours
        colors = self._colorize_data(data)

        mesh = make_geom(vertices, self._faces, colors)
        renderer = MyRenderer()
        renderer._displayed_pickable_objects.add(mesh)
        renderer.build_display()
        self._renderer = renderer

    def _repr_html_(self):
        if self._renderer is None:
            if self._analysis_type == "code_aster":
                data = [x for x in self._point_data if "DISP" in x][-1]
            else:
                data = self._point_data[0]
            self._viz_geom(data)

        renderer = self._renderer
        display(HBox([VBox([HBox(renderer._controls), renderer._renderer]), renderer.html]))
