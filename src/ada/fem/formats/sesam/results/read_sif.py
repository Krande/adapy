from __future__ import annotations

import numpy as np
import pathlib
from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING, Iterator

from ada.fem.formats.sesam.common import sesam_eltype_2_general
from ada.fem.formats.sesam.read import cards

if TYPE_CHECKING:
    from ada.fem.results.common import ElementFieldData, FEAResult, Mesh, NodalFieldData


@dataclass
class SifReader:
    file: Iterator

    nodes: np.ndarray = None
    node_ids: np.ndarray = None
    elements: list[tuple] = None
    results: list[tuple] = field(default_factory=list)

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
            line = next(self.file)
            stripped = line.strip()

            if stripped.startswith(startswith) is False and stripped[0].isnumeric() is False and stripped[0] != "-":
                self._last_line = line
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

    def eval_flags(self, line: str):
        stripped = line.strip()

        # Nodes
        if stripped.startswith("GCOORD"):
            self.nodes = np.array(list(self.read_gcoords(stripped)))
        if stripped.startswith("GNODE"):
            self.node_ids = np.array(list(self.read_gnodes(stripped)))

        # Elements
        if stripped.startswith("GELMNT1"):
            self.elements = list(self.read_gelmnts(stripped))

        # Results
        if stripped.startswith(cards.RVNODDIS.name):  # Nodal
            self.results.append((cards.RVNODDIS.name, list(self.read_results(cards.RVNODDIS.name, stripped))))

    def load(self):
        while True:
            try:
                line = next(self.file)
                self.eval_flags(line)
            except StopIteration:
                break


def read_sif_file(sif_file: str | pathlib.Path) -> FEAResult:
    from ada.fem.results.common import FEAResult, FEATypes

    sif_file = pathlib.Path(sif_file)

    with open(sif_file, "r") as f:
        sif = SifReader(f)
        sif.load()

    mesh = get_sif_mesh(sif)
    results = get_sif_results(sif)

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


def get_sif_results(sif: SifReader) -> list[ElementFieldData | NodalFieldData]:
    result_blocks = []
    for res in sif.results:
        if res[0] == cards.RVNODDIS.name:
            result_blocks += get_nodal_results(res[1])

    return result_blocks
