import pathlib

import numpy as np
from IPython.display import display
from ipywidgets import Dropdown, HBox, VBox

from .common import (
    get_edges_and_faces_from_mesh,
    get_edges_from_fem,
    get_faces_from_fem,
    get_vertices_from_fem,
    magnitude,
)
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
    faces = np.asarray(get_faces_from_fem(fem), dtype="uint16").ravel()

    res = [magnitude(u_) for u_ in u]
    max_r = max(res)
    res_norm_col = np.asarray([(x / max_r, 0, 0) for x in res], dtype="float32")

    render_mesh(vertices, faces, res_norm_col)


class Results:
    def __init__(self, result_file_path, part=None, palette=None):
        self.palette = [(0, 149 / 255, 239 / 255), (1, 0, 0)] if palette is None else palette
        self._analysis_type = None
        self._point_data = []
        self._cell_data = []
        self._results_file_path = result_file_path
        self._read_result_file(result_file_path)
        self._part = part
        self._renderer = None
        self._render_sets = None
        self._undeformed_mesh = None
        self._deformed_mesh = None

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

    @property
    def mesh_undeformed(self):
        return self._undeformed_mesh

    @property
    def mesh_deformed(self):
        """

        :return:
        :rtype: pythreejs.Mesh
        """
        return self._deformed_mesh

    def _get_mesh(self, file_ref):
        import meshio

        file_ref = pathlib.Path(file_ref)
        if file_ref.suffix.lower() == ".rmed":
            mesh = meshio.read(file_ref, "med")
            self._analysis_type = "code_aster"
        else:
            mesh = meshio.read(file_ref)

        return mesh

    def _read_part(self, file_ref):
        mesh = self._get_mesh(file_ref)
        self._mesh = mesh
        self._vertices = np.asarray(get_vertices_from_fem(self._part.fem), dtype="float32")
        self._faces = np.asarray(get_faces_from_fem(self._part.fem), dtype="uint16").ravel()
        self._edges = np.asarray(get_edges_from_fem(self._part.fem), dtype="uint16").ravel()

        for n in mesh.point_data.keys():
            self._point_data.append(n)

        for n in mesh.cell_data.keys():
            self._cell_data.append(n)

    def _read_result_file(self, file_ref):
        mesh = self._get_mesh(file_ref)
        self._mesh = mesh
        self._vertices = np.asarray(mesh.points, dtype="float32")

        edges, faces = get_edges_and_faces_from_mesh(mesh)
        self._edges = np.asarray(edges, dtype="uint16").ravel()
        self._faces = np.asarray(faces, dtype="uint16").ravel()

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

    def create_viz_geom(self, data_type, displ_data=False, renderer=None):
        """

        :param data_type:
        :param displ_data:
        :type renderer: ada.base.renderer.MyRenderer
        :return:
        """
        default_vertex_color = (8, 8, 8)

        data = np.asarray(self.mesh.point_data[data_type], dtype="float32")
        colors = self._colorize_data(data)

        if renderer is None:
            renderer = MyRenderer()
            self._renderer = renderer

        # deformations
        if displ_data is True:
            vertices = np.asarray([x + u[:3] for x, u in zip(self._vertices, data)], dtype="float32")
            if self._undeformed_mesh is None:
                dark_grey = (0.66, 0.66, 0.66)
                white_color = np.asarray([dark_grey for x in self._vertices], dtype="float32")
                o_mesh = faces_to_mesh("undeformed", self._vertices, self._faces, white_color, opacity=0.5)
                self._undeformed_mesh = o_mesh
                renderer._displayed_non_pickable_objects.add(o_mesh)
        else:
            vertices = self._vertices
            if self._undeformed_mesh is not None:
                renderer._displayed_non_pickable_objects.remove(self._undeformed_mesh)
                self._undeformed_mesh = None

        vertices = np.array(vertices, dtype=np.float32)

        # Colours
        mesh = faces_to_mesh("deformed", vertices, self._faces, colors)
        points = vertices_to_mesh("deformed_vertices", vertices, default_vertex_color)
        lines = edges_to_mesh("deformed_lines", vertices, self._edges, default_vertex_color)

        if self._deformed_mesh is None:
            self._deformed_mesh = (mesh, points, lines)
            renderer._displayed_pickable_objects.add(mesh)
            renderer._displayed_pickable_objects.add(points)
            renderer._displayed_pickable_objects.add(lines)
            renderer.build_display(camera_type="perspective")
        else:
            face_geom = self._deformed_mesh[0].geometry
            face_geom.attributes["position"].array = vertices
            face_geom.attributes["index"].array = self._faces
            face_geom.attributes["color"].array = colors

            point_geom = self._deformed_mesh[1].geometry
            point_geom.attributes["position"].array = vertices

            edge_geom = self._deformed_mesh[2].geometry
            edge_geom.attributes["position"].array = vertices
            edge_geom.attributes["index"].array = self._edges

    def on_changed_point_data_set(self, p):
        data = p["new"]
        if self._analysis_type == "code_aster":
            if "point_tags" in data:
                print("\r" + "Point Tags are not a valid display value" + 10 * " ", end="")
                return None
            if "DISP" in data:
                self.create_viz_geom(data, displ_data=True, renderer=self.renderer)
            else:
                self.create_viz_geom(data, renderer=self.renderer)

    def _repr_html_(self):
        if self._renderer is None:
            self._renderer = MyRenderer()
            if self._analysis_type == "code_aster":
                data = [x for x in self._point_data if "DISP" in x][-1]
                self.create_viz_geom(data, displ_data=True, renderer=self.renderer)
                i = self._point_data.index(data)
                self._render_sets = Dropdown(
                    options=self._point_data, value=self._point_data[i], tooltip="Select a set", disabled=False
                )
                self._render_sets.observe(self.on_changed_point_data_set, "value")
                self.renderer._controls.pop()
                self.renderer._controls.append(self._render_sets)
            else:
                raise NotImplementedError(f'Support for analysis_type "{self._analysis_type}"')

        display(HBox([VBox([HBox(self.renderer._controls), self.renderer._renderer]), self.renderer.html]))

    def __repr__(self):
        return f"Results({self._analysis_type}, {self._results_file_path.name})"
