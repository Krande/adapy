from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

import numpy as np

from ada.base.types import BaseEnum
from ada.fem.formats.calculix.results.get_version_from_frd import (
    extract_calculix_version,
)
from ada.fem.results.common import (
    CellBlockData,
    ElementBlock,
    ElementInfo,
    FEAResult,
    FemNodes,
    Mesh,
    MeshData,
)
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
    #: A two-node truss: ccx writes a T3D2 to the .frd as itself (type 11), not as the brick it solves it with.
    LINE = "line"

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
        elif shape[1] == 6:
            return ElemShape.LINE
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
        elif shape == ElemShape.LINE:
            from ada.fem.shapes.definitions import LineShapes

            return LineShapes.LINE
        else:
            raise NotImplementedError(f"{shape=}")


#: The ``ICTYPE`` of a ``100C`` record (CalculiX GraphiX manual, "Nodal Results Block"): the analysis the block is a
#: result of. 0 static, 1 time step, 2 frequency (a mode), 3 load step, 4 user named. Measured, ccx 2.23: a
#: ``*FREQUENCY`` step's blocks carry 2 (and ``MODAL`` in ``ANALYS``), a ``*STATIC`` step's 0.
FRD_ANALYSIS_KINDS = {0: "static", 1: "transient", 2: "eigen", 3: "static"}


def frd_analysis_type(line: str) -> int | None:
    """``ICTYPE`` of a ``100C`` record, read by its fixed columns (``1X,'100','C',6A1,E12.5,I12,20A1,I2,...``): the
    20-character text field before it may be blank or not, so the line is not split on blanks."""
    i = line.find("100C")
    field_ = line[i + 54 : i + 56] if i >= 0 else ""
    try:
        return int(field_)
    except ValueError:
        return None


def frd_analysis_kind(types) -> str | None:
    """One kind for the blocks of a file: the kind of their analysis type when they agree, else None."""
    kinds = {FRD_ANALYSIS_KINDS.get(t) for t in types}
    return kinds.pop() if len(kinds) == 1 else None


@dataclass
class CcxResultModel:
    file: Iterator

    ccx_version: str = None
    nodes: np.ndarray = None
    elements: np.ndarray = None
    results: list[NodalFieldData | ElementFieldData] = field(default_factory=list)

    #: The analysis type of every result block (the ``ICTYPE`` of its ``100C`` record, :func:`frd_analysis_type`).
    analysis_types: set = field(default_factory=set)

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

        # Each element is appended when the NEXT element's ``-1`` header
        # arrives. The final element has no following header — the loop
        # exits on the block terminator (``-3``) instead — so flush the
        # last accumulated element here or it's silently dropped. This
        # lost exactly one element per mesh (e.g. a corner element of the
        # Calculix shell→solid expansion), leaving a hole in the render.
        if len(curr_element) != 0:
            elements.append(tuple(curr_element))

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

        # Calculix declares more component names than the per-node rows
        # actually carry. A DISP block, for instance, ships four `-5`
        # lines (`D1 D2 D3 ALL`) but only three numeric columns in the
        # `-1` rows — `ALL` is a magnitude aggregate the writer never
        # serialises per node. The downstream bake (`FEAResultStreamAdapter
        # .field_specs`) uses `len(components)` to size the AFBL blob
        # stride, so without trimming we'd promise four columns and
        # `write_field_blob_streaming` would assert
        # `arr.shape != (n_points, n_components)` — which is why every
        # `ccx_shell_*` / `ccx_solid_*` case in the verification report
        # baked but its mode posters fell back to "figures unavailable".
        # Truncate to the actual data width sampled from the first row.
        if component_data and component_names:
            n_data_cols = len(component_data[0]) - 1  # drop nid column
            if n_data_cols < len(component_names):
                component_names = component_names[:n_data_cols]

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
            # "1PSTEP <result block> <increment> <step>": measured on a two-step ccx 2.23 deck, "1PSTEP 1 1 1" and
            # "1PSTEP 2 1 2". The increment was read as the step, so every step's fields came out as step 1.
            split_data = stripped.split()
            self._curr_step = int(float(split_data[3] if len(split_data) > 3 else split_data[2]))

        if stripped.startswith("1PMODE"):
            split_data = stripped.split()
            self._curr_mode = int(float(split_data[-1]))

        if stripped.startswith("1PGK"):
            split_data = stripped.split()
            self._curr_eig_value = float(split_data[1])

        if stripped.startswith("100CL"):
            split_data = stripped.split()
            self._curr_eig_freq = float(split_data[2])
            ictype = frd_analysis_type(data)
            if ictype is not None:
                self.analysis_types.add(ictype)

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


def read_from_frd_file(frd_file) -> MeshData:
    with open(frd_file, "r") as f:
        ccx_res_model = CcxResultModel(f)
        ccx_res_model.load()

    mesh = to_mesh_data(ccx_res_model)
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
    blocks = []
    # ccx writes no element of a user element type (U1) to the .frd -- measured, ccx 2.23: a deck of U1 beams only
    # has an empty "3C" block -- so a beam model's result is its nodes and their fields.
    if np.asarray(ccx_results.elements).size > 0:
        shape = ElemShape.get_type_from_elem_array_shape(ccx_results.elements)
        node_refs = ccx_results.elements[:, 4:]
        elem_info = ElementInfo(
            type=ElemShape.el_shape_to_baseshape(shape), source_software=FEATypes.CALCULIX, source_type=str(shape.value)
        )
        identifiers = ccx_results.elements[:, 0]
        blocks.append(ElementBlock(elem_info=elem_info, node_refs=node_refs, identifiers=identifiers))

    coords = ccx_results.nodes[:, 1:]
    identifiers = ccx_results.nodes[:, 0]
    nodes = FemNodes(coords=coords, identifiers=identifiers)
    mesh = Mesh(elements=blocks, nodes=nodes)
    graft_deck_sections(mesh, frd_file.with_suffix(".inp"))

    software_version = extract_calculix_version(frd_file)

    # Eigen analyses: the participation factors + effective modal mass
    # (global axes, 6 DOF) live in the sibling .dat, not the .frd field
    # stream. Parse them when present so they surface via get_eig_summary.
    eigen_mode_data = _read_eigen_mode_data(ccx_results, frd_file)

    return FEAResult(
        frd_file.name,
        FEATypes.CALCULIX,
        ccx_results.results,
        mesh=mesh,
        results_file_path=frd_file,
        description=description,
        software_version=software_version,
        eigen_mode_data=eigen_mode_data,
        analysis_kind=frd_analysis_kind(ccx_results.analysis_types),
    )


#: A ``*BEAM SECTION`` with a geometric profile (not ``SECTION=GENERAL``): the only kind a deck's
#: beams can be drawn as solids from.
_PROFILED_BEAM_SECTION = re.compile(r"^\*\s*BEAM\s+SECTION\b(?![^\n]*SECTION\s*=\s*GENERAL)", re.I | re.M)


def graft_deck_sections(mesh: Mesh, inp_file: pathlib.Path) -> int:
    """Give ``mesh`` the beam sections of the input deck beside the ``.frd``; how many elements got one.

    The ``.frd`` holds no section, but the deck that was solved does, and CalculiX writes its
    results next to it under the same name. Taken only by element id and end-node position
    (:func:`~ada.fem.results.line_sections.graft_line_sections`), so it applies where the ``.frd``
    holds the deck's line elements (``*NODE FILE, OUTPUT=2D``). Without it ccx writes each beam as
    the brick it expanded it into, under new nodes: that result is a solid already, and nothing
    matches. A deck beam the ``.frd`` left out entirely is added back when its end nodes are in
    the result. A deck whose beams all have ``SECTION=GENERAL`` (area and inertias, no outline --
    adapy's ``U1`` beams) is not read: it has nothing to draw.
    """
    from ada.config import logger

    if not inp_file.is_file():
        return 0
    try:
        if _PROFILED_BEAM_SECTION.search(inp_file.read_text(errors="replace")) is None:
            return 0
        import ada
        from ada.fem.results.line_sections import (
            graft_line_sections,
            line_section_tables,
        )

        assembly = ada.from_fem(inp_file)
        parts = assembly.get_all_parts_in_assembly(include_self=True)
        lines = [el for p in parts if p.fem is not None for el in p.fem.elements.lines]
        return graft_line_sections(mesh, line_section_tables(lines), add_missing=True)
    except Exception as exc:  # noqa: BLE001 - the sections are decoration; the result reads without them
        logger.warning(f"beam sections of {inp_file.name} not read: {exc}")
        return 0


def _read_eigen_mode_data(ccx_results: CcxResultModel, frd_file: pathlib.Path):
    """Return the EigenDataSummary from the sibling ``.dat`` for an eigen
    run, or ``None``. Calculix writes the modal parameters to the ``.dat``;
    a frequency step is signalled by per-mode ``eigen_freq`` on the field
    results."""
    is_eigen = any(getattr(r, "eigen_freq", None) is not None for r in ccx_results.results)
    if not is_eigen:
        return None
    dat_file = frd_file.with_suffix(".dat")
    if not dat_file.exists():
        return None
    from ada.fem.formats.calculix.results.read_eigen_data import get_eigen_data

    try:
        summary = get_eigen_data(dat_file)
    except Exception as exc:  # noqa: BLE001 — modal mass is supplementary
        from ada.config import logger

        logger.warning(f"Could not read eigen modal data from {dat_file.name}: {exc}")
        return None
    return summary if summary.modes else None


def to_mesh_data(ccx_results: CcxResultModel) -> MeshData:
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
    cell_block = CellBlockData(cell_type=str(shape.value), data=cells)

    # Point Data — only the last step is exposed via the mesh-data
    # interface (matches the prior meshio.Mesh behaviour).
    point_data: dict[str, np.ndarray] = dict()
    for res in ccx_results.get_last_step_results(CcxPointData):
        values = np.asarray(res.values)[:, 1:]
        point_data[res.name] = values

    # Field (cell) data — list of one entry per cell block.
    cell_data: dict[str, list[np.ndarray]] = dict()
    for res in ccx_results.get_last_step_results(CcxFieldData):
        values = np.asarray(res.values)[:, 1:]
        cell_data[res.name] = [values]

    return MeshData(points=points, cells=[cell_block], cell_data=cell_data, point_data=point_data)


def safesplit(stripped):
    return list(filter(lambda x: x.strip() != "", re.split(r"(?=\s|(?<=[0-9])-(?=[0-9]))", stripped)))
