from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

import meshio
import numpy as np

from ada.base.types import BaseEnum
from ada.fem.results.common import ElementBlock, ElementInfo, FEAResult, FemNodes, Mesh
from ada.fem.results.field_data import (
    ElementFieldData,
    FieldData,
    NodalFieldData,
    NodalFieldType,
)


class ReadFrdFailedException(Exception):
    pass


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
    WEDGE15 = "wedge15"
    HEX = "hexahedron"
    HEX20 = "hexahedron20"
    TETRA = "tetra"
    TETRA10 = "tetra10"

    @staticmethod
    def get_type_from_elem_array_shape(elements: np.ndarray) -> ElemShape:
        shape = elements.shape
        if shape[1] == 10:
            return ElemShape.WEDGE
        elif shape[1] == 12:
            return ElemShape.HEX
        elif shape[1] == 8:
            return ElemShape.TETRA
        elif shape[1] == 24:
            return ElemShape.HEX20
        elif shape[1] == 19:
            return ElemShape.WEDGE15
        elif shape[1] == 14:
            return ElemShape.TETRA10
        else:
            raise NotImplementedError(f"{shape=}")

    @staticmethod
    def el_shape_to_baseshape(shape: ElemShape):
        from ada.fem.shapes.definitions import SolidShapes

        if shape == ElemShape.HEX:
            return SolidShapes.HEX8
        elif shape == ElemShape.HEX20:
            return SolidShapes.HEX20
        elif shape == ElemShape.WEDGE:
            return SolidShapes.WEDGE
        elif shape == ElemShape.TETRA:
            return SolidShapes.TETRA
        elif shape == ElemShape.WEDGE15:
            return SolidShapes.WEDGE15
        elif shape == ElemShape.TETRA10:
            return SolidShapes.TETRA10
        else:
            raise NotImplementedError(f"{shape=}")


@dataclass
class CcxResultModel:
    file: Iterator

    ccx_version: str = None
    nodes: np.ndarray = None
    elements: np.ndarray = None
    results: list[NodalFieldData | ElementFieldData] = field(default_factory=list)

    _curr_step: int = None
    _curr_mode: int = None
    _curr_eig_freq: float = None
    _curr_eig_value: float = None

    def collect_nodes(self):
        while True:
            data = next(self.file)
            stripped = data.strip()
            if stripped.startswith("-1") is False:
                break
            split = stripped.split()
            try:
                yield [float(x) for x in split[1:]]
            except ValueError:  # Typically it's a - character separating two columns not whitespace
                split = list(filter(lambda x: x.strip() != "", re.split(r"(?=\s|(?<=[0-9])-(?=[0-9]))", stripped)))
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
                if len(curr_element) != 0:
                    elements.append(tuple(curr_element))
                data = [int(x) for x in split[1:]]
                curr_element = data
            elif stripped.startswith("-2"):
                data = [int(x) for x in split[1:]]
                curr_element += data
            else:
                break

        self.elements = np.asarray(elements)
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

        curr_step = self._curr_step
        curr_eig_freq = None
        curr_eig_value = None

        if self._curr_mode is not None:
            curr_step = self._curr_mode
            curr_eig_value = self._curr_eig_value
            curr_eig_freq = self._curr_eig_freq

        field_type = None
        if name.startswith("DISP"):
            field_type = NodalFieldType.DISP

        self.results.append(
            NodalFieldData(
                name,
                curr_step,
                component_names,
                np.asarray(component_data),
                eigen_freq=curr_eig_freq,
                eigen_value=curr_eig_value,
                field_type=field_type,
            )
        )
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

        if stripped.startswith("1PMODE"):
            split_data = stripped.split()
            self._curr_mode = int(float(split_data[-1]))

        if stripped.startswith("1PGK"):
            split_data = stripped.split()
            self._curr_eig_value = float(split_data[1])

        if stripped.startswith("100CL"):
            split_data = stripped.split()
            self._curr_eig_freq = float(split_data[2])

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
    with open(frd_file, "r", encoding="utf8") as f:
        ccx_res_model = CcxResultModel(f)
        ccx_res_model.load()

    if ccx_res_model.elements is None:
        raise ReadFrdFailedException("No element information from Calculix")

    return to_fea_result_obj(ccx_res_model, frd_file)


def to_fea_result_obj(ccx_results: CcxResultModel, frd_file) -> FEAResult:
    from ada.fem.formats.general import FEATypes

    if isinstance(frd_file, str):
        frd_file = pathlib.Path(frd_file)

    description = f"Adapy - Calculix ({ccx_results.ccx_version}) Results"
    shape = ElemShape.get_type_from_elem_array_shape(ccx_results.elements)
    node_refs = ccx_results.elements[:, 4:]
    elem_info = ElementInfo(
        type=ElemShape.el_shape_to_baseshape(shape), source_software=FEATypes.CALCULIX, source_type=str(shape.value)
    )
    identifiers = ccx_results.elements[:, 0]
    elem_block = ElementBlock(elem_info=elem_info, node_refs=node_refs, identifiers=identifiers)

    coords = ccx_results.nodes[:, 1:]
    identifiers = ccx_results.nodes[:, 0]
    nodes = FemNodes(coords=coords, identifiers=identifiers)
    mesh = Mesh(elements=[elem_block], nodes=nodes)

    return FEAResult(
        frd_file.name,
        FEATypes.CALCULIX,
        ccx_results.results,
        mesh=mesh,
        results_file_path=frd_file,
        description=description,
    )


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
    elements = ccx_results.elements
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


def safesplit(stripped):
    return list(filter(lambda x: x.strip() != "", re.split(r"(?=\s|(?<=[0-9])-(?=[0-9]))", stripped)))
