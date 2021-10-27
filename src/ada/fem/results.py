from __future__ import annotations

import logging
import os
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import List, Tuple, Union

import meshio
import numpy as np
import pythreejs
from IPython.display import display
from ipywidgets import Dropdown, HBox, VBox

from ada.fem.formats import FEATypes
from ada.visualize.femviz import get_edges_and_faces_from_meshio, magnitude
from ada.visualize.renderer_pythreejs import MyRenderer
from ada.visualize.threejs_utils import edges_to_mesh, faces_to_mesh, vertices_to_mesh

from .concepts.eigenvalue import EigenDataSummary


class Results:
    def __init__(self, res_path, name=None, fem_format=None, assembly=None, palette=None, output=None, overwrite=True):
        self._name = name
        self._visualizer = ResultsMesh(palette, fem_format=fem_format, parent=self)
        self._eigen_mode_data = None
        self._fem_format = fem_format
        self._assembly = assembly
        self._output = output
        self._overwrite = overwrite
        self._results_file_path = pathlib.Path(res_path)
        self._read_result_file(self.results_file_path)

    def _read_result_file(self, file_ref, overwrite=False):
        if file_ref.exists() is False:
            return None

        mesh = self._get_results_from_result_file(file_ref)

        if mesh is None:
            return None

        print(f'Importing meshio.Mesh from result file "{file_ref}"')
        self.result_mesh.add_results(mesh)

    def _get_results_from_result_file(self, file_ref, overwrite=False):
        from .formats.abaqus.results import read_abaqus_results
        from .formats.calculix.results import read_calculix_results
        from .formats.code_aster.results import read_code_aster_results
        from .formats.sesam.results import read_sesam_results

        file_ref = pathlib.Path(file_ref)
        suffix = file_ref.suffix.lower()

        res_map = {
            ".rmed": (read_code_aster_results, FEATypes.CODE_ASTER),
            ".frd": (read_calculix_results, FEATypes.CALCULIX),
            ".odb": (read_abaqus_results, FEATypes.ABAQUS),
            ".sin": (read_sesam_results, FEATypes.SESAM),
        }
        res_reader, fem_format = res_map.get(suffix, (None, None))

        if res_reader is None:
            logging.error(f'Results class currently does not support filetype "{suffix}"')
            return None

        self.fem_format = fem_format

        return res_reader(self, file_ref, overwrite)

    def save_output(self, dest_file) -> None:
        if self.output is None or self.output.stdout is None:
            print("No output is found")
            return None
        dest_file = pathlib.Path(dest_file)

        os.makedirs(dest_file.parent, exist_ok=True)
        with open(dest_file, "w") as f:
            f.write(self.output.stdout)

    @property
    def name(self):
        return self._name

    @property
    def fem_format(self):
        return self._fem_format

    @fem_format.setter
    def fem_format(self, value):
        if value not in FEATypes.all:
            raise ValueError(f'Unsupported FEA Type "{value}"')
        self._fem_format = value

    @property
    def output(self) -> subprocess.CompletedProcess:
        return self._output

    @property
    def results_file_path(self):
        return self._results_file_path

    @results_file_path.setter
    def results_file_path(self, value):
        self._results_file_path = value

    @property
    def result_mesh(self) -> ResultsMesh:
        return self._visualizer

    @property
    def assembly(self):
        """:rtype: ada.Assembly"""
        return self._assembly

    @property
    def eigen_mode_data(self) -> EigenDataSummary:
        return self._eigen_mode_data

    @eigen_mode_data.setter
    def eigen_mode_data(self, value: EigenDataSummary):
        self._eigen_mode_data = value

    def _repr_html_(self):

        if self.result_mesh.renderer is None:
            res = self.result_mesh.build_renderer()
        else:
            res = True

        if res is False:
            return

        p3s_renderer = self.result_mesh.renderer
        display(HBox([VBox([HBox(p3s_renderer.controls), p3s_renderer.renderer]), p3s_renderer.html]))

    def __repr__(self):
        return f"Results({self._fem_format}, {self._results_file_path.name})"


@dataclass
class ResultsMesh:

    palette: List[tuple]
    parent: Results
    fem_format: str
    renderer: MyRenderer = None
    render_sets: Dropdown = None
    mesh: meshio.Mesh = None
    undeformed_mesh: Union[None, pythreejs.Mesh] = None
    deformed_mesh: Tuple[pythreejs.Mesh, pythreejs.Points, pythreejs.LineSegments] = None
    point_data: List = field(default_factory=list)
    cell_data: List = field(default_factory=list)

    vertices: np.ndarray = None
    edges: np.ndarray = None
    faces: np.ndarray = None

    def __post_init__(self):
        self.palette = [(0, 149 / 255, 239 / 255), (1, 0, 0)] if self.palette is None else self.palette

    def add_results(self, mesh: meshio.Mesh):
        self.mesh = mesh
        self.vertices = np.asarray(mesh.points, dtype="float32")

        edges, faces = get_edges_and_faces_from_meshio(mesh)
        self.edges = np.asarray(edges, dtype="uint16").ravel()
        self.faces = np.asarray(faces, dtype="uint16").ravel()

        for n in mesh.point_data.keys():
            self.point_data.append(n)

        for n in mesh.cell_data.keys():
            self.cell_data.append(n)

    def build_renderer(self) -> bool:
        self.renderer = MyRenderer()
        if len(self.point_data) == 0:
            return False
        if self.fem_format == FEATypes.CODE_ASTER:
            data = [x for x in self.point_data if "DISP" in x][-1]
        elif self.fem_format == FEATypes.CALCULIX:
            data = [x for x in self.point_data if "U" in x][-1]
        else:
            raise NotImplementedError(f'Support for analysis_type "{self.fem_format}"')

        self.create_viz_geom(data, displ_data=True, renderer=self.renderer)
        i = self.point_data.index(data)
        self.render_sets = Dropdown(
            options=self.point_data, value=self.point_data[i], tooltip="Select a set", disabled=False
        )
        self.render_sets.observe(self.on_changed_point_data_set, "value")
        self.renderer.controls.pop()
        self.renderer.controls.append(self.render_sets)
        return True

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
        default_vertex_color = (8, 8, 8)

        data = np.asarray(self.mesh.point_data[data_type], dtype="float32")
        colors = self._colorize_data(data)

        if renderer is None:
            renderer = MyRenderer()
            self.renderer = renderer

        # deformations
        if displ_data is True:
            vertices = np.asarray([x + u[:3] for x, u in zip(self.vertices, data)], dtype="float32")
            if self.undeformed_mesh is None:
                dark_grey = (0.66, 0.66, 0.66)
                white_color = np.asarray([dark_grey for x in self.vertices], dtype="float32")
                o_mesh = faces_to_mesh("undeformed", self.vertices, self.faces, white_color, opacity=0.5)
                self.undeformed_mesh = o_mesh
                renderer._displayed_non_pickable_objects.add(o_mesh)
        else:
            vertices = self.vertices
            if self.undeformed_mesh is not None:
                renderer._displayed_non_pickable_objects.remove(self.undeformed_mesh)
                self.undeformed_mesh = None

        vertices = np.array(vertices, dtype=np.float32)

        # Colours
        mesh = faces_to_mesh("deformed", vertices, self.faces, colors)
        points = vertices_to_mesh("deformed_vertices", vertices, default_vertex_color)
        lines = edges_to_mesh("deformed_lines", vertices, self.edges, default_vertex_color)

        if self.deformed_mesh is None:
            self.deformed_mesh = (mesh, points, lines)
            renderer.displayed_pickable_objects.add(mesh)
            renderer.displayed_pickable_objects.add(points)
            renderer.displayed_pickable_objects.add(lines)
            renderer.build_display(camera_type="perspective")
        else:
            face_geom = self.deformed_mesh[0].geometry
            face_geom.attributes["position"].array = vertices
            face_geom.attributes["index"].array = self.faces
            face_geom.attributes["color"].array = colors

            point_geom = self.deformed_mesh[1].geometry
            point_geom.attributes["position"].array = vertices

            edge_geom = self.deformed_mesh[2].geometry
            edge_geom.attributes["position"].array = vertices
            edge_geom.attributes["index"].array = self.edges

    def on_changed_point_data_set(self, p):
        data = p["new"]
        if self.fem_format == FEATypes.CODE_ASTER:
            is_displ = True if "DISP" in data else False
            if "point_tags" in data:
                print("\r" + "Point Tags are not a valid display value" + 10 * " ", end="")
                return None
        elif self.fem_format == FEATypes.CALCULIX:
            is_displ = True if "U" in data else False
        else:
            return None

        if is_displ:
            self.create_viz_geom(data, displ_data=True, renderer=self.renderer)
        else:
            self.create_viz_geom(data, renderer=self.renderer)


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
