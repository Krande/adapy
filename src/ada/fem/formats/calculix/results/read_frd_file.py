from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

import meshio
import numpy as np

from ada.base.types import BaseEnum
from ada.fem.results.common import ElementBlock, FEAResult, FemNodes, Mesh
from ada.fem.results.field_data import FieldData


class ReadFrdFailedException(Exception):
    ...


class CcxPointData(BaseEnum):
    DISP = "DISP"
    FORC = "FORC"
    STRESS = "STRESS"
    PE = "PE"
    ERROR = "ERROR"


class CcxFieldData(BaseEnum):
    pass


class ElemShape(Enum):
    WEDGE = "wedge"
    HEX = "hexahedron"
    TET = "tetra"

    @staticmethod
    def get_type_from_elem_array_shape(elements: np.ndarray) -> ElemShape:
        shape = elements.shape
        if shape[1] == 10:
            return ElemShape.WEDGE
        elif shape[1] == 12:
            return ElemShape.HEX
        elif shape[1] == 8:
            return ElemShape.TET
        else:
            raise NotImplementedError(f"{shape=}")


@dataclass
class CcxResultModel:
    file: Iterator

    ccx_version: str = None
    nodes: np.ndarray = None
    elements: list[tuple] = None
    results: list[FieldData] = field(default_factory=list)

    _curr_step: int = None

    def collect_nodes(self):
        while True:
            data = next(self.file)
            stripped = data.strip()
            if stripped.startswith("-1") is False:
                break
            split = stripped.split()
            yield [float(x) for x in split[1:]]

    def collect_elements(self):
        elements = []
        curr_element = []
        while True:
            data = next(self.file)

            stripped = data.strip()
            if stripped.startswith("-1") is False:
                if stripped.startswith("-2") is False:
                    break

            split = stripped.split()

            if stripped.startswith("-1"):
                data = [int(x) for x in split[1:]]
                curr_element = data
            elif stripped.startswith("-2"):
                data = [int(x) for x in split[1:]]
                curr_element += data
                elements.append(tuple(curr_element))
                curr_element = []
            else:
                break

        self.elements = elements
        self.eval_flags(data)

    def collect_results(self, first_line):
        name = first_line.split()[1]

        component_names = []
        component_data = []
        while True:
            data = next(self.file)
            stripped = data.strip()

            if stripped.startswith("-5"):
                split = stripped.split()
                component_names.append(split[1])
            elif stripped.startswith("-1"):
                slen = len(stripped)
                x = 12
                res = [stripped[i : i + x] for i in range(0, slen, x)]
                first_batch = res[0].split()
                nid = int(float(first_batch[1]))
                component_data.append((nid, *[float(x) for x in res[1:]]))
            else:
                break

        self.results.append(FieldData(name, self._curr_step, component_names, component_data))
        self.eval_flags(data)

    def eval_flags(self, data: str):
        stripped = data.strip()
        if stripped.startswith("1UVERSION"):
            res = stripped.split()
            self.ccx_version = res[-1].lower().replace("version", "").strip()

        if stripped.startswith("2C"):
            split = stripped.split()
            num_len = int(float(split[1]))
            # Note! np.fromiter have issues on older numpy versions such as (1.22.3)
            self.nodes = np.fromiter(self.collect_nodes(), dtype=np.dtype((float, 4)), count=num_len)

        if stripped.startswith("3C"):
            self.collect_elements()

        if stripped.startswith("1PSTEP"):
            split_data = stripped.split()
            self._curr_step = int(float(split_data[2]))

        if stripped.startswith("-4"):
            self.collect_results(stripped)

    def load(self):
        while True:
            try:
                curr = next(self.file)
                self.eval_flags(curr)
            except StopIteration:
                break

    def get_last_step_results(self, data_type: BaseEnum) -> list[FieldData]:
        results = dict()
        for res in self.results:
            if data_type.from_str(res.name) is None:
                continue
            existing_step = results.get(res.name)
            if existing_step is not None:
                if res.step > existing_step.step:
                    results[res.name] = res
            else:
                results[res.name] = res

        return list(results.values())


def read_from_frd_file(frd_file) -> meshio.Mesh:
    with open(frd_file, "r") as f:
        ccx_res_model = CcxResultModel(f)
        ccx_res_model.load()

    mesh = to_meshio_mesh(ccx_res_model)
    return mesh


def read_from_frd_file_proto(frd_file) -> FEAResult:
    with open(frd_file, "r") as f:
        ccx_res_model = CcxResultModel(f)
        ccx_res_model.load()

    mesh = to_fea_result_obj(ccx_res_model)
    return mesh


def to_meshio_mesh(ccx_results: CcxResultModel) -> meshio.Mesh:
    # Points
    nodes = ccx_results.nodes
    if nodes is None:
        raise ReadFrdFailedException("No nodes found. Maybe there was an issue with the analysis")

    points = nodes[:, 1:]
    monotonic_point_map = dict()
    for i, x in enumerate(nodes[:, 0].astype(int), start=0):
        monotonic_point_map[x] = i

    # Cells
    elements = np.asarray(ccx_results.elements)
    cells = elements[:, 4:]
    for original_num, new_num in monotonic_point_map.items():
        cells[cells == original_num] = new_num

    shape = ElemShape.get_type_from_elem_array_shape(elements)
    cell_block = meshio.CellBlock(str(shape.value), cells)

    # Point Data
    # Multiple steps are AFAIK not supported in the meshio Mesh object. So only the last step is used
    point_data = dict()
    for res in ccx_results.get_last_step_results(CcxPointData):
        values = np.asarray(res.values)[:, 1:]
        point_data[res.name] = values

    # Field Data
    cell_data = dict()
    for res in ccx_results.get_last_step_results(CcxFieldData):
        values = np.asarray(res.values)[:, 1:]
        cell_data[res.name] = [values]

    mesh = meshio.Mesh(points=points, cells=[cell_block], cell_data=cell_data, point_data=point_data)
    return mesh


def to_fea_result_obj(ccx_results: CcxResultModel) -> FEAResult:
    from ada.fem.formats.general import FEATypes

    name = f"Adapy - Calculix ({ccx_results.ccx_version}) Results"

    elem_block = ElementBlock()
    nodes = FemNodes()

    mesh = Mesh(elements=[elem_block], nodes=nodes)

    return FEAResult(name, FEATypes.CALCULIX, ccx_results.results, mesh=mesh)
