from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING, Iterator

import numpy as np

from ada.fem.formats.sesam.common import sesam_eltype_2_general
from ada.fem.formats.sesam.read import cards

if TYPE_CHECKING:
    from ada.fem.results.common import ElementFieldData, FEAResult, Mesh, NodalFieldData

STRESS_MAP = {
    1: ("SIGXX", "Normal Stress x-direction"),
    2: ("SIGYY", "Normal Stress y-direction"),
    4: ("TAUXY", "Shear stress in y-direction, yz-plane"),
}
INT_LOCATIONS = {24: [()]}
RESULT_CARDS = [cards.RVNODDIS, cards.RVSTRESS, cards.RDPOINTS, cards.RDSTRESS, cards.RDIELCOR, cards.RDRESREF]


@dataclass
class SifReader:
    file: Iterator

    nodes: np.ndarray = None
    node_ids: np.ndarray = None
    elements: list[tuple] = None
    results: list[tuple] = field(default_factory=list)

    skipped_flags: list[str] = field(default_factory=list)
    _last_line: str = None

    def _read_single_line_statements(self, startswith: str, first_line: str):
        first_vals = [float(x) for x in first_line.split()[1:]]
        yield first_vals

        while True:
            line = next(self.file)
            stripped = line.strip()

            if stripped.startswith(startswith) is False:
                self._last_line = line
                break

            yield [float(x) for x in stripped.split()[1:]]

    def _read_multi_line_statements(self, startswith: str, first_line: str):
        curr_elements = [float(x) for x in first_line.split()[1:]]
        while True:
            stripped = next(self.file).strip()

            if stripped.startswith(startswith) is False and stripped[0].isnumeric() is False and stripped[0] != "-":
                self._last_line = stripped
                yield curr_elements
                break

            if stripped.startswith(startswith):
                yield curr_elements
                curr_elements = [float(x) for x in stripped.split()[1:]]
            else:
                curr_elements += [float(x) for x in stripped.split()]

    def read_gcoords(self, first_line: str):
        for data in self._read_single_line_statements("GCOORD", first_line):
            yield data

        self.eval_flags(self._last_line)

    def read_gnodes(self, first_line: str):
        for data in self._read_single_line_statements("GNODE", first_line):
            yield data[:2]

        self.eval_flags(self._last_line)

    def read_gelmnts(self, first_line: str):
        elno_id, eltyp, members = cards.GELMNT1.get_indices_from_names(["elno", "eltyp", "nids"])
        for data in self._read_multi_line_statements("GELMNT1", first_line):
            yield data[eltyp], data[elno_id], data[members:]

        self.eval_flags(self._last_line)

    def read_results(self, result_variable: str, first_line: str) -> tuple:
        for data in self._read_multi_line_statements(result_variable, first_line):
            yield data

        self.eval_flags(self._last_line)

    def eval_flags(self, line: str):
        stripped = line.strip()
        is_skipped_flag = False

        # Nodes
        if stripped.startswith("GCOORD"):
            self.nodes = np.array(list(self.read_gcoords(stripped)))
        elif stripped.startswith("GNODE"):
            self.node_ids = np.array(list(self.read_gnodes(stripped)))

        # Elements
        elif stripped.startswith("GELMNT1"):
            self.elements = list(self.read_gelmnts(stripped))
        elif stripped[0].isnumeric() is False and stripped[0] != "-":
            is_skipped_flag = True

        # Results
        for res_card in RESULT_CARDS:
            if stripped.startswith(res_card.name):
                self.results.append((res_card.name, list(self.read_results(res_card.name, stripped))))
                is_skipped_flag = False

        if is_skipped_flag:
            flag = stripped.split()[0]
            if flag not in self.skipped_flags:
                self.skipped_flags.append(flag)

    def load(self):
        while True:
            try:
                line = next(self.file)
                self.eval_flags(line)
            except StopIteration:
                break

    def get_result(self, name: str) -> list:
        result = list(filter(lambda x: x[0] == name, self.results))
        if len(result) != 1:
            raise NotImplementedError("")

        return result

    def get_rdpoints_map(self) -> dict:
        rdpoints = self.get_result(cards.RDPOINTS.name)
        return {int(x[1]): x for x in rdpoints[0][1][1:]}

    def get_rdstress_map(self) -> dict:
        rdstress = self.get_result(cards.RDSTRESS.name)
        return {int(x[1]): tuple([int(i) for i in x[3:]]) for x in rdstress[0][1]}

    def get_rdielcor_map(self) -> dict:
        rdielcor = self.get_result(cards.RDIELCOR.name)
        return {int(x[1]): x[2:] for x in rdielcor[0][1]}


def read_sif_file(sif_file: str | pathlib.Path) -> FEAResult:
    from ada.fem.results.common import FEAResult, FEATypes

    sif_file = pathlib.Path(sif_file)

    with open(sif_file, "r") as f:
        sif = SifReader(f)
        sif.load()

    mesh = get_sif_mesh(sif)
    results = get_sif_results(sif, mesh)

    return FEAResult(sif_file.stem, FEATypes.SESAM, results=results, mesh=mesh)


def get_sif_mesh(sif: SifReader) -> Mesh:

    from ada.fem.results.common import (
        ElementBlock,
        ElementInfo,
        FEATypes,
        FemNodes,
        Mesh,
    )

    nodes = FemNodes(coords=sif.nodes[:, 1:], identifiers=sif.node_ids[:, 0])
    elem_blocks = []
    for eltype, elements in groupby(sif.elements, key=lambda x: x[0]):
        elem_type = int(eltype)
        elem_data = list(elements)
        elem_identifiers = np.array([x[1] for x in elem_data], dtype=int)
        elem_node_refs = np.array([x[2] for x in elem_data], dtype=float)
        res = sesam_eltype_2_general(elem_type)
        elem_info = ElementInfo(type=res, source_software=FEATypes.SESAM, source_type=elem_type)
        elem_blocks.append(ElementBlock(elem_info=elem_info, node_refs=elem_node_refs, identifiers=elem_identifiers))

    return Mesh(elements=elem_blocks, nodes=nodes)


def get_nodal_results(res) -> list[NodalFieldData]:
    from ada.fem.results.common import NodalFieldData

    comps = "U1|", "U2|", "U3|", "U4|", "U5|", "U6|"
    indices = cards.RVNODDIS.get_indices_from_names(["inod", *comps])
    nid = indices[0]
    start = indices[1]
    stop = indices[-1] + 1
    results = []
    for ires, data in groupby(res[1:], key=lambda x: x[1]):
        field_data = np.asarray([(x[nid], *x[start:stop]) for x in data], dtype=float)
        fd = NodalFieldData(cards.RVNODDIS.name, int(ires), [x.replace("|", "") for x in comps], field_data)
        results.append(fd)

    return results


def get_sif_results(sif: SifReader, mesh: Mesh) -> list[ElementFieldData | NodalFieldData]:
    result_blocks = []
    for res in sif.results:
        if res[0] == cards.RVNODDIS.name:
            result_blocks += get_nodal_results(res[1])

    result_blocks += get_stresses(sif)

    return result_blocks


def _get_rdpoints_nox_data(rdpoints_res, nlay_i, nsptra_len):
    nox_data = rdpoints_res[nlay_i + 1 : -nsptra_len]
    nox_data_clean = dict()
    data_iter = iter(nox_data)
    init_el = next(data_iter)
    if init_el == -1:
        remaining_el = 0
    else:
        remaining_el = 3
    curr_el_id = int(init_el)
    nox_data_clean[curr_el_id] = []

    while True:
        try:
            curr_el = next(data_iter)
        except StopIteration:
            break

        if remaining_el > 0:
            nox_data_clean[curr_el_id].append(curr_el)
            remaining_el -= 1
        else:
            curr_el_id = int(curr_el)
            if curr_el_id == -1:
                remaining_el = 0
            else:
                remaining_el = 3
                nox_data_clean[curr_el_id] = []

    return nox_data_clean


def _iter_stress(rv_stresses: Iterator, sif, nsp) -> Iterator:
    rdstress_map = sif.get_rdstress_map()
    ires_i, iielno_i, ispalt_i, irstrs_i = cards.RVSTRESS.get_indices_from_names(["ires", "iielno", "ispalt", "irstrs"])
    for rv_stress in rv_stresses:
        iielno = int(rv_stress[iielno_i])
        irstrs = int(rv_stress[irstrs_i])
        rdstress_res = rdstress_map[irstrs]
        data = np.array(rv_stress[irstrs_i + 1 :])
        for i, data_per_int in enumerate(data.reshape((nsp, len(rdstress_res))), start=1):
            yield iielno, i, *data_per_int


def get_int_positions(sif, rdpoints_res) -> list:
    ieltyp_i, icoref_i, ijkdim_i = cards.RDPOINTS.get_indices_from_names(["ieltyp", "icoref", "ijkdim"])
    rdielcor_map = sif.get_rdielcor_map()
    ijkdim = rdpoints_res[ijkdim_i]
    nok = int(ijkdim / 10000)
    noj = int((ijkdim % 10000) / 100)
    noi = int(ijkdim % 100)
    rdielcor_res = rdielcor_map[int(rdpoints_res[icoref_i])]
    el_type = int(rdpoints_res[ieltyp_i])
    print(nok, noj, noi, rdielcor_res, el_type)


def get_stresses(sif: SifReader) -> list[ElementFieldData | NodalFieldData]:
    from ada.fem.results.common import ElementFieldData

    ires_i, ispalt_i, irstrs_i = cards.RVSTRESS.get_indices_from_names(["ires", "ispalt", "irstrs"])
    nsp_i, nsptra_i, nlay_i = cards.RDPOINTS.get_indices_from_names(["nsp", "nsptra", "nlay"])
    rdstress_map = sif.get_rdstress_map()
    rdpoints_map = sif.get_rdpoints_map()

    def keyfunc(x):
        return x[ires_i], x[ispalt_i], x[irstrs_i]

    field_results = []
    field_pos = ElementFieldData.field_pos.INT
    for (ires, ispalt, irstrs), rv_stresses in groupby(sif.get_result(cards.RVSTRESS.name)[0][1][1:], key=keyfunc):
        rdpoints_res = rdpoints_map[ispalt]
        _ = get_int_positions(sif, rdpoints_res)
        stress_types = [STRESS_MAP[c][0] for c in rdstress_map[irstrs]]
        data = np.array(list(_iter_stress(rv_stresses, sif, int(rdpoints_res[nsp_i]))))
        field_data = ElementFieldData("STRESS", int(ires), components=stress_types, values=data, field_pos=field_pos)
        field_results.append(field_data)

    return field_results
