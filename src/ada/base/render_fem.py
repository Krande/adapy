import pathlib

import numpy as np
from IPython.display import display
from ipywidgets import Dropdown, HBox, VBox

from .common import get_fem_edges, get_fem_faces, magnitude
from .renderer import MyRenderer
from .threejs_geom import edges_to_mesh, faces_to_mesh, vertices_to_mesh


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
    faces = np.asarray(get_fem_faces(fem), dtype="uint16").ravel()

    res = [magnitude(u_) for u_ in u]
    max_r = max(res)
    res_norm_col = np.asarray([(x / max_r, 0, 0) for x in res], dtype="float32")

    render_mesh(vertices, faces, res_norm_col)


class Results:
    def __init__(self, part, result_file, palette=None):
        self.palette = [(0, 149 / 255, 239 / 255), (1, 0, 0)] if palette is None else palette
        self._part = part
        self._analysis_type = None
        self._point_data = []
        self._cell_data = []
        self._read_result_file(result_file)
        self._renderer = None
        self._render_sets = None

    @property
    def mesh(self):
        return self._mesh

    @property
    def renderer(self):
        """

        :return:
        :rtype: ada.base.renderer.MyRenderer
        """
        return self._renderer

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
        self._faces = np.asarray(get_fem_faces(self._part.fem), dtype="uint16").ravel()
        self._edges = np.asarray(get_fem_edges(self._part.fem), dtype="uint16").ravel()

        for n in mesh.point_data.keys():
            self._point_data.append(n)

        for n in mesh.cell_data.keys():
            self._cell_data.append(n)

    def _colorize_data(self, data, func=magnitude):
        res = [func(d) for d in data]
        sorte = sorted(res)
        min_r = sorte[0]
        max_r = sorte[-1]

        start = np.array(self.palette[0])
        end = np.array(self.palette[-1])

        def curr_p(t):
            return start + (end - start) * t / (max_r - min_r)

        colors = np.asarray([curr_p(x) for x in res], dtype="float32")
        return colors

    def _viz_geom(self, data_type, displ_data=False):
        """

        :param data_type:
        :param displ_data:
        :return:
        """

        data = np.asarray(self.mesh.point_data[data_type], dtype="float32")

        renderer = MyRenderer()

        # deformations
        if displ_data:
            vertices = np.asarray([x + u[:3] for x, u in zip(self._vertices, data)], dtype="float32")
            white_color = np.asarray([(245 / 255, 245 / 255, 245 / 255) for x in self._vertices], dtype="float32")
            o_mesh = faces_to_mesh("undeformed", self._vertices, self._faces, white_color, opacity=0.5)
            renderer._displayed_pickable_objects.add(o_mesh)
        else:
            vertices = self._vertices

        # Colours
        colors = self._colorize_data(data)
        mesh = faces_to_mesh("deformed", vertices, self._faces, colors)
        default_vertex_color = (8, 8, 8)
        points = vertices_to_mesh("deformed_vertices", vertices, default_vertex_color)
        lines = edges_to_mesh("deformed_lines", vertices, self._edges, default_vertex_color)
        renderer._displayed_pickable_objects.add(mesh)
        renderer._displayed_pickable_objects.add(points)
        renderer._displayed_pickable_objects.add(lines)

        renderer.build_display(camera_type="perspective")
        self._renderer = renderer

    def on_changed_point_data_set(self, p):

        data = p["new"]
        if self._analysis_type == "code_aster":
            if "DISP" in data:
                self._viz_geom(data, displ_data=True)
            else:
                self._viz_geom(data)
            print(f'Changing set to "{p}"')

        self.renderer.build_display()

    def _repr_html_(self):
        if self._renderer is None:
            if self._analysis_type == "code_aster":
                data = [x for x in self._point_data if "DISP" in x][-1]
                self._viz_geom(data, displ_data=True)
                i = self._point_data.index(data)
                self._render_sets = Dropdown(
                    options=self._point_data, value=self._point_data[i], tooltip="Select a set", disabled=False
                )
                self._render_sets.observe(self.on_changed_point_data_set, "value")
                self.renderer._controls.pop()
                self.renderer._controls.append(self._render_sets)
            else:
                raise NotImplementedError(f'Support for analysis_type "{self._analysis_type}"')

        renderer = self._renderer
        display(HBox([VBox([HBox(renderer._controls), renderer._renderer]), renderer.html]))
