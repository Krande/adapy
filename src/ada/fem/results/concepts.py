from __future__ import annotations

import os
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List

import meshio
import numpy as np

from ada.config import logger
from ada.fem.formats.general import FEATypes
from ada.visit.rendering.femviz import get_edges_and_faces_from_meshio, magnitude

from ..formats.abaqus.results import read_abaqus_results
from ..formats.calculix.results import read_calculix_results
from ..formats.code_aster.results import read_code_aster_results
from ..formats.sesam.results import read_sesam_results
from .eigenvalue import EigenDataSummary

if TYPE_CHECKING:
    from ada import Assembly


class Results:
    res_map = {
        ".rmed": (read_code_aster_results, FEATypes.CODE_ASTER),
        ".frd": (read_calculix_results, FEATypes.CALCULIX),
        ".odb": (read_abaqus_results, FEATypes.ABAQUS),
        ".sin": (read_sesam_results, FEATypes.SESAM),
    }

    def __init__(
        self,
        res_path,
        name: str = None,
        fem_format: str | FEATypes = None,
        assembly: None | Assembly = None,
        palette=None,
        output=None,
        overwrite=True,
        metadata=None,
        import_mesh=False,
    ):
        if isinstance(fem_format, str):
            fem_format = FEATypes.from_str(fem_format)

        self._name = name
        self._visualizer = ResultsMesh(palette, fem_format=fem_format, parent=self)
        self._eigen_mode_data = None
        self._fem_format = fem_format
        self._assembly = assembly
        self._output = output
        self._overwrite = overwrite
        self._metadata = metadata if metadata is not None else dict()
        self._results_file_path = pathlib.Path(res_path) if res_path is not None else None
        self._user_data = dict()
        self._history_output = None
        self._import_mesh = import_mesh
        if res_path is not None:
            self.load_data_from_result_file(self.results_file_path)
            if self.results_file_path.exists():
                self._last_modified = os.path.getmtime(str(self.results_file_path))
            else:
                self._last_modified = None

    def load_data_from_result_file(self, file_ref=None, overwrite=False):
        file_ref = self.results_file_path if file_ref is None else file_ref

        if file_ref is None:
            return None

        if file_ref.exists() is False:
            return None

        mesh = self._get_results_from_result_file(file_ref)

        if mesh is None:
            return None

        if self._import_mesh is False:
            return None

        print(f'Importing meshio.Mesh from result file "{file_ref}"')
        self.result_mesh.add_results(mesh)

    def _get_results_from_result_file(self, file_ref, overwrite=False):
        file_ref = pathlib.Path(file_ref)
        suffix = file_ref.suffix.lower()

        res_reader, fem_format = Results.res_map.get(suffix, (None, None))

        if res_reader is None:
            logger.error(f'Results class currently does not support filetype "{suffix}"')
            return None

        self.fem_format = fem_format

        return res_reader(self, file_ref, overwrite)

    def save_results_to_excel(self, dest_file, filter_components_by_name=None):
        """This method is just a sample for how certain results can easily be exported to Excel"""

        try:
            import xlsxwriter
        except ModuleNotFoundError:
            raise ModuleNotFoundError(
                "xlsxwriter must be installed to export to xlsx.\n"
                'To install you can use "conda install -c conda-forge xlsxwriter"'
            )

        dest_file = pathlib.Path(dest_file).with_suffix(".xlsx")

        workbook = xlsxwriter.Workbook(dest_file)
        worksheet = workbook.add_worksheet()

        worksheet.write("A1", "Step")
        worksheet.write("B1", "Element")
        worksheet.write("C1", "ForceComponent")
        worksheet.write("D1", "Value")
        i = 2
        for step in self.history_output.steps:
            for el_name, el in step.element_data.items():
                el: ElementDataOutput
                for force_name, force in el.forces.items():
                    if filter_components_by_name is not None:
                        skip_it = False
                        if force.name.lower() not in [x.lower() for x in filter_components_by_name]:
                            skip_it = True
                        if skip_it:
                            continue
                    worksheet.write(f"A{i}", step.name)
                    worksheet.write(f"B{i}", el_name)
                    worksheet.write(f"C{i}", force.name)
                    worksheet.write(f"D{i}", force.final_force)
                    i += 1

        workbook.close()

    @property
    def name(self):
        return self._name

    @property
    def fem_format(self) -> FEATypes:
        return self._fem_format

    @fem_format.setter
    def fem_format(self, value: FEATypes):
        if isinstance(value, str):
            value = FEATypes.from_str(value)
            if value is None:
                raise ValueError(f'Unsupported FEA Type "{value}"')
        self._fem_format = value

    @property
    def last_modified(self):
        return self._last_modified

    @last_modified.setter
    def last_modified(self, value):
        self._last_modified = value

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
    def assembly(self) -> "Assembly":
        return self._assembly

    @property
    def eigen_mode_data(self) -> EigenDataSummary:
        return self._eigen_mode_data

    @eigen_mode_data.setter
    def eigen_mode_data(self, value: EigenDataSummary):
        self._eigen_mode_data = value

    @property
    def history_output(self) -> ResultsHistoryOutput:
        return self._history_output

    @history_output.setter
    def history_output(self, value: ResultsHistoryOutput):
        self._history_output = value

    @property
    def metadata(self):
        return self._metadata

    @property
    def user_data(self) -> dict:
        return self._user_data

    def __repr__(self):
        return f"Results({self._fem_format}, {self._results_file_path.name})"


@dataclass
class ElemForceComp:
    name: str
    data: List[tuple]

    @property
    def final_force(self):
        return self.data[-1][-1]


@dataclass
class ElementDataOutput:
    name: str
    displacements: dict[int, List[tuple]] = field(default_factory=dict)
    forces: dict[int, ElemForceComp] = field(default_factory=dict)

    @property
    def final_displ(self):
        return {x: y[-1][-1] for x, y in self.displacements.items()}

    @property
    def final_forces(self):
        return {x: y.data[-1][-1] for x, y in self.forces.items()}


@dataclass
class FEMDataOutput:
    name: str
    data: List[tuple]


@dataclass
class HistoryStepDataOutput:
    name: str
    step_type: str
    element_data: Dict[str, ElementDataOutput] = field(default_factory=dict)
    fem_data: Dict[str, FEMDataOutput] = field(default_factory=dict)


@dataclass
class ResultsHistoryOutput:
    steps: List[HistoryStepDataOutput] = field(default_factory=list)


@dataclass
class ResultsMesh:
    palette: list[tuple]
    parent: Results
    fem_format: str
    renderer: object = None
    render_sets: object = None
    mesh: meshio.Mesh = None
    undeformed_mesh: None | object = None
    deformed_mesh: tuple[object, object, object] = None
    point_data: list = field(default_factory=list)
    cell_data: list = field(default_factory=list)

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

    def colorize_data(self, data, func=magnitude):
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
