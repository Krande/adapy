import logging
import pathlib
import subprocess
import traceback

import numpy as np
from IPython.display import display
from ipywidgets import Dropdown, HBox, VBox

from ada.visualize.femviz import (
    get_edges_and_faces_from_meshio,
    get_edges_from_fem,
    get_faces_from_fem,
    get_vertices_from_fem,
    magnitude,
)
from ada.visualize.renderer import MyRenderer
from ada.visualize.threejs_utils import edges_to_mesh, faces_to_mesh, vertices_to_mesh

from .concepts.eigenvalue import EigenDataSummary


class Results:
    def __init__(self, name, res_path, fem_format, part=None, assembly=None, palette=None, output=None, overwrite=True):
        self._name = name
        self._fem_format = fem_format
        self.palette = [(0, 149 / 255, 239 / 255), (1, 0, 0)] if palette is None else palette
        self._eigen_mode_data = None
        self._analysis_type = None
        self._point_data = []
        self._cell_data = []
        self._assembly = assembly
        self._part = part
        self._renderer = None
        self._render_sets = None
        self._undeformed_mesh = None
        self._deformed_mesh = None
        self._output = output
        self._overwrite = overwrite
        self._results_file_path = pathlib.Path(res_path)
        self._read_result_file(self._results_file_path)

    @property
    def name(self):
        return self._name

    @property
    def fem_format(self):
        return self._fem_format

    @property
    def output(self) -> subprocess.CompletedProcess:
        return self._output

    @property
    def results_file_path(self):
        return self._results_file_path

    @property
    def mesh(self):
        return self._mesh

    @property
    def renderer(self):
        """:rtype: ada.visualize.renderer.MyRenderer"""
        return self._renderer

    @property
    def mesh_undeformed(self):
        return self._undeformed_mesh

    @property
    def mesh_deformed(self):
        """:rtype: pythreejs.Mesh"""
        return self._deformed_mesh

    @property
    def part(self):
        """:rtype: ada.Part"""
        return self._part

    @property
    def assembly(self):
        """:rtype: ada.Assembly"""
        return self._assembly

    @property
    def eigen_mode_data(self) -> EigenDataSummary:
        return self._eigen_mode_data

    def _get_mesh(self, file_ref):
        import meshio

        from ada.core.utils import get_list_of_files
        from ada.fem import Step
        from ada.fem.elements import ElemShapes

        file_ref = pathlib.Path(file_ref)
        suffix = file_ref.suffix.lower()
        if suffix in ".rmed":
            from ada.fem.io.code_aster.reader import med_to_fem

            self._analysis_type = "code_aster"

            if self.assembly.fem.steps[0].type == Step.TYPES.EIGEN:
                from .io.code_aster.results import get_eigen_data

                self._eigen_mode_data = get_eigen_data(file_ref)
            fem = med_to_fem(file_ref, "temp")
            if any([x.type in ElemShapes.tri7 for x in fem.elements.shell]):
                logging.error("Meshio does not support 7 node Triangle elements yet")
                return None
            mesh = meshio.read(file_ref, "med")
        elif suffix == ".frd":
            from ccx2paraview import Converter

            self._analysis_type = "calculix"
            if file_ref.exists() is False:
                return None
            if len(get_list_of_files(file_ref.parent, ".vtu")) == 0 or self._overwrite is True:
                convert = Converter(str(file_ref), ["vtu"])
                convert.run()
            result_files = get_list_of_files(file_ref.parent, ".vtu")
            if len(result_files) == 0:
                raise FileNotFoundError("No VTU files found. Check if analysis was successfully completed")

            if len(result_files) > 1:
                logging.error("Currently only reading last step for multi-step Calculix analysis results")

            result_file = result_files[-1]
            self._results_file_path = pathlib.Path(result_file)
            print(f'Reading result from "{result_file}"')
            mesh = meshio.read(result_file)
            dat_file = file_ref.with_suffix(".dat")
            if dat_file.exists() and self.assembly.fem.steps[0].type == Step.TYPES.EIGEN:
                from .io.calculix.results import get_eigen_data

                self._eigen_mode_data = get_eigen_data(dat_file)
        else:
            logging.error(f'Results class currently does not support filetype "{suffix}"')
            return None

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
        if file_ref.exists() is False:
            return None
        try:
            mesh = self._get_mesh(file_ref)
        except ValueError as e:

            logging.error(f'Error during result file reading. "{e}". {traceback.format_exc()}')
            return None

        if mesh is None:
            return None
        self._mesh = mesh
        self._vertices = np.asarray(mesh.points, dtype="float32")

        edges, faces = get_edges_and_faces_from_meshio(mesh)
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

    def create_viz_geom(self, data_type, displ_data=False, renderer: MyRenderer = None) -> None:
        """

        :param data_type:
        :param displ_data:
        :param renderer:
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
            is_displ = True if "DISP" in data else False
            if "point_tags" in data:
                print("\r" + "Point Tags are not a valid display value" + 10 * " ", end="")
                return None
        elif self._analysis_type == "calculix":
            is_displ = True if "U" in data else False
        else:
            return None

        if is_displ:
            self.create_viz_geom(data, displ_data=True, renderer=self.renderer)
        else:
            self.create_viz_geom(data, renderer=self.renderer)

    def _repr_html_(self):
        if self._renderer is None:
            self._renderer = MyRenderer()
            if self._analysis_type == "code_aster":
                data = [x for x in self._point_data if "DISP" in x][-1]
            elif self._analysis_type == "calculix":
                data = [x for x in self._point_data if "U" in x][-1]
            else:
                raise NotImplementedError(f'Support for analysis_type "{self._analysis_type}"')

            self.create_viz_geom(data, displ_data=True, renderer=self.renderer)
            i = self._point_data.index(data)
            self._render_sets = Dropdown(
                options=self._point_data, value=self._point_data[i], tooltip="Select a set", disabled=False
            )
            self._render_sets.observe(self.on_changed_point_data_set, "value")
            self.renderer._controls.pop()
            self.renderer._controls.append(self._render_sets)

        display(HBox([VBox([HBox(self.renderer._controls), self.renderer._renderer]), self.renderer.html]))

    def __repr__(self):
        return f"Results({self._analysis_type}, {self._results_file_path.name})"


def get_fem_stats(fem_file, dest_md_file, data_file="data.json"):
    """

    :param fem_file:
    :param dest_md_file:
    :param data_file: Destination of data.json file (keeping track of last modified status etc..)
    """
    import json
    import os

    from ada import Assembly
    from ada.fem.utils import get_eldata

    dest_md_file = pathlib.Path(dest_md_file)
    data_file = pathlib.Path(data_file)
    a = Assembly()
    a.read_fem(fem_file)

    out_str = ""

    for name, part in a.parts.items():
        fem = part.fem
        r = get_eldata(fem_source=fem)
        if len(r.keys()) == 0:
            continue
        out_str += f"* **{name}**: ("

        el_data = ""
        for el_type, el_num in r.items():
            el_data += f"{el_type}: {el_num}, "

        out_str += el_data[:-2] + ")\n"

    os.makedirs(dest_md_file.parent, exist_ok=True)

    with open(dest_md_file, "w") as f:
        f.write(out_str)

    if data_file.exists():
        with open(data_file, "r") as f:
            data = json.load(f)
    else:
        data = dict()
    print(data)
