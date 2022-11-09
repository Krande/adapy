from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING, Iterator

import numpy as np

from ada.core.utils import Counter
from ada.fem.formats.sesam.common import sesam_eltype_2_general
from ada.fem.formats.sesam.read import cards
from ada.sections.categories import BaseTypes

if TYPE_CHECKING:
    from ada import Section
    from ada.fem.results.common import ElementFieldData, FEAResult, Mesh, NodalFieldData

FEM_SEC_NAME = Counter(prefix="FS")

STRESS_MAP = {
    1: ("SIGXX", "Normal Stress x-direction"),
    2: ("SIGYY", "Normal Stress y-direction"),
    4: ("TAUXY", "Shear stress in y-direction, yz-plane"),
}
FORCE_MAP = {
    1: ("NXX", "Normal force in x-direction, yz-plane"),
    2: ("NXY", "Shear force in y-direction, yz-plane"),
    3: ("NXZ", "Shear force in z-direction, yz-plane"),
    10: ("MXX", "Torsion moment around x-axis, yz-plane"),
    11: ("MXY", "Bending moment around y-axis, yz-plane"),
    12: ("MXZ", "Bending moment around z-axis, yz-plane"),
}

SEC_MAP = {
    "GIORH": (
        BaseTypes.IPROFILE,
        (("hz", "h"), ("ty", "t_w"), ("bt", "w_top"), ("tt", "t_ftop"), ("bb", "w_btn"), ("tb", "t_fbtn")),
    )
}
MAT_MAP = {
    "MISOSEL": (
        ("young", "E"),
        ("poiss", "v"),
        ("rho", "rho"),
        ("damp", "zeta"),
        ("alpha", "alpha"),
        ("yield", "sig_y"),
    )
}
# Integration Point location.
# If Integration point is in nodal position
# (Int ID, Node ID, Thickness offset)
# Else
# (Int ID, (X relative Coord, Y relative Coord, Thickness offset))

INT_LOCATIONS = {
    24: [
        (0, 0, -0.5),
        (1, 1, -0.5),
        (2, (0.5, 0.5, -0.5)),
        (3, 2, -0.5),
        (4, 3, -0.5),
        (5, 0, 0.5),
        (6, 1, 0.5),
        (7, (0.5, 0.5, 0.5)),
        (8, 2, 0.5),
        (9, 3, 0.5),
    ],
    15: [(0, 0), (1, 0.5), (2, 1)],
}
OTHER_CARDS = [cards.GUNIVEC, cards.TDSECT, cards.TDMATER, cards.MISOSEL, cards.MORSMEL]
SECTION_CARDS = [cards.GIORH, cards.GBOX]
RESULT_CARDS = [
    cards.RVNODDIS,
    cards.RVSTRESS,
    cards.RDPOINTS,
    cards.RDSTRESS,
    cards.RDIELCOR,
    cards.RDRESREF,
    cards.RVFORCES,
    cards.RDFORCES,
]


@dataclass
class SifReader:
    file: Iterator

    nodes: np.ndarray = None
    node_ids: np.ndarray = None
    elements: list[tuple] = None
    results: list[tuple] = field(default_factory=list)

    skipped_flags: list[str] = field(default_factory=list)
    _last_line: str = None

    _gelref1: list = None
    _other: dict[str, list] = field(default_factory=dict)
    _sections: dict[str, list] = field(default_factory=dict)

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

    def get_sections(self) -> dict[int, Section]:
        from ada import Section

        sec_map: dict[str, cards.DataCard] = {s.name: s for s in SECTION_CARDS}
        td_sect_map = self.get_tdsect_map()

        sections = dict()
        for sec_name, sec_data in self._sections.items():
            sec_card = sec_map[sec_name]
            if len(sec_data) != 1:
                raise NotImplementedError()
            res = sec_card.get_data_map_from_names(["geono", "hz", "ty", "bt", "tt", "bb", "tb"], sec_data[0])
            sec_id = int(float(res["geono"]))
            sm = SEC_MAP[sec_name]
            sec_type = sm[0]
            prop_map = {ada_n: res[ses_n] for ses_n, ada_n in sm[1]}
            sec_name = td_sect_map.get(sec_id)[-1]
            sec = Section(name=sec_name, sec_id=sec_id, sec_type=sec_type, **prop_map)
            sections[sec_id] = sec

        return sections

    def get_materials(self):
        _ = {x[1]: x[-1] for x in self._other.get("TDMATER")}

        for _ in self._other.get("TDMATER"):
            print("sd")

    def get_gelref(self):
        return self._gelref1

    def eval_flags(self, line: str):
        stripped = line.strip()
        is_skipped_flag = False

        # Nodes
        if stripped.startswith(cards.GCOORD.name):
            self.nodes = np.array(list(self.read_gcoords(stripped)))
        elif stripped.startswith(cards.GNODE.name):
            self.node_ids = np.array(list(self.read_gnodes(stripped)))

        # Elements
        elif stripped.startswith(cards.GELMNT1.name):
            self.elements = list(self.read_gelmnts(stripped))

        elif stripped[0].isnumeric() is False and stripped[0] != "-":
            is_skipped_flag = True

        # Sections
        if stripped.startswith(cards.GELREF1.name):
            self._gelref1 = list(cards.GELREF1.iter(self.file, stripped, next_func=self.eval_flags))

        for other_card in OTHER_CARDS:
            if stripped.startswith(other_card.name):
                self._other[other_card.name] = list(other_card.iter(self.file, stripped, next_func=self.eval_flags))

        for sec_card in SECTION_CARDS:
            if stripped.startswith(sec_card.name):
                self._sections[sec_card.name] = list(sec_card.iter(self.file, stripped, next_func=self.eval_flags))

        # if len(self._other) > 0 and self._gelref1 is not None and len(self._sections) > 0:
        #     self.read_fem_sections()

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
        if len(result) > 1:
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

    def get_rdforces_map(self) -> dict:
        rdforces = self.get_result(cards.RDFORCES.name)
        return {int(x[1]): tuple([int(i) for i in x[3:]]) for x in rdforces[0][1]}

    def get_tdsect_map(self):
        res = self._other.get("TDSECT")
        if res is None:
            raise ValueError("TDSECT is not yet imported from SIF file")
        return {x[1]: x for x in res}


def read_sif_file(sif_file: str | pathlib.Path) -> FEAResult:
    sif_file = pathlib.Path(sif_file)

    with open(sif_file, "r") as f:
        sif = SifReader(f)
        sif.load()

    s2m = Sif2Mesh(sif)
    fea_results = s2m.convert(sif_file)

    return fea_results


@dataclass
class Sif2Mesh:
    sif: SifReader

    mesh: Mesh = None

    def convert(self, sif_file) -> FEAResult:
        from ada.fem.results.common import FEAResult, FEATypes

        self.mesh = self.get_sif_mesh()
        results = self.get_sif_results()

        return FEAResult(sif_file.stem, FEATypes.SESAM, results=results, mesh=self.mesh)

    def get_sif_mesh(self) -> Mesh:
        from ada.fem.results.common import (
            ElementBlock,
            ElementInfo,
            FEATypes,
            FemNodes,
            Mesh,
        )
        from ada.fem.shapes.definitions import ShapeResolver

        sif = self.sif

        _ = self.sif.get_sections()
        _ = self.sif.get_materials()
        _ = cards.GELREF1.cast_to_structured_np(
            ["elno", "matno", "geono", "transno"], self.sif.get_gelref(), ["elid", "matid", "secid", "transid"]
        )
        nodes = FemNodes(coords=sif.nodes[:, 1:], identifiers=sif.node_ids[:, 0])
        elem_blocks = []
        for eltype, elements in groupby(sif.elements, key=lambda x: x[0]):
            elem_type = int(eltype)
            elem_data = list(elements)
            general_elem_type = sesam_eltype_2_general(elem_type)
            num_nodes = ShapeResolver.get_el_nodes_from_type(general_elem_type)
            elem_identifiers = np.array([x[1] for x in elem_data], dtype=int)
            elem_node_refs = np.array([x[2][:num_nodes] for x in elem_data], dtype=float)
            res = sesam_eltype_2_general(elem_type)
            elem_info = ElementInfo(type=res, source_software=FEATypes.SESAM, source_type=elem_type)
            elem_blocks.append(
                ElementBlock(elem_info=elem_info, node_refs=elem_node_refs, identifiers=elem_identifiers)
            )

        return Mesh(elements=elem_blocks, nodes=nodes)

    def get_sif_results(self) -> list[ElementFieldData | NodalFieldData]:
        result_blocks = self.get_nodal_data()
        result_blocks += self.get_field_data()

        return result_blocks

    def get_nodal_data(self) -> list[NodalFieldData]:
        return get_nodal_results(self.sif.get_result(cards.RVNODDIS.name)[0][1])

    def get_field_data(self) -> list[ElementFieldData | NodalFieldData]:
        sif = self.sif
        field_results = []
        if len(sif.get_result(cards.RVSTRESS.name)) > 0:
            field_results += self.get_field_shell_data()

        if len(sif.get_result(cards.RVFORCES.name)) > 0:
            field_results += self.get_field_line_data()

        return field_results

    def get_field_line_data(self):
        ires_i, ispalt_i, irforc_i = cards.RVFORCES.get_indices_from_names(["ires", "ispalt", "irforc|"])
        nsp_i, eltyp_i = cards.RDPOINTS.get_indices_from_names(["nsp", "ieltyp"])

        rdpoints_map = self.sif.get_rdpoints_map()

        def keyfunc(x):
            return x[ires_i], x[ispalt_i], x[irforc_i]

        field_results = []

        force_res_name = cards.RVFORCES.name
        for (ires, ispalt, irforc), rv_forces in groupby(self.sif.get_result(force_res_name)[0][1][1:], key=keyfunc):
            rdpoints_res = rdpoints_map[ispalt]
            elem_type = int(rdpoints_res[eltyp_i])
            nsp = int(rdpoints_res[nsp_i])
            field_data = self._get_line_field_data(rv_forces, int(ires), int(irforc), elem_type, nsp)
            field_results.append(field_data)

        return field_results

    def _get_line_field_data(self, rv_forces, ires, irforc, elem_type, nsp) -> ElementFieldData:
        from ada.fem.results.common import ElementFieldData

        rdforces_map = self.sif.get_rdforces_map()
        force_types = [FORCE_MAP[c][0] for c in rdforces_map[irforc]]
        data = np.array(list(_iter_line_forces(rv_forces, rdforces_map, nsp)))
        return ElementFieldData(
            "FORCES",
            int(ires),
            components=force_types,
            values=data,
            field_pos=ElementFieldData.field_pos.INT,
            int_positions=INT_LOCATIONS[elem_type],
        )

    def get_field_shell_data(self):
        sif = self.sif
        ires_i, ispalt_i, irstrs_i = cards.RVSTRESS.get_indices_from_names(["ires", "ispalt", "irstrs"])
        nsp_i, eltyp_i = cards.RDPOINTS.get_indices_from_names(["nsp", "ieltyp"])

        rdpoints_map = sif.get_rdpoints_map()

        def keyfunc(x):
            return x[ires_i], x[ispalt_i], x[irstrs_i]

        field_results = []

        for (ires, ispalt, irstrs), rv_stresses in groupby(sif.get_result(cards.RVSTRESS.name)[0][1][1:], key=keyfunc):
            rdpoints_res = rdpoints_map[ispalt]
            nsp = int(rdpoints_res[nsp_i])
            elem_type = int(rdpoints_res[eltyp_i])

            field_data = self._get_shell_field_data(rv_stresses, ires, irstrs, elem_type, nsp)
            field_results.append(field_data)

        return field_results

    def _get_shell_field_data(self, rv_stresses, ires, irstrs, elem_type: int, nsp) -> ElementFieldData:
        from ada.fem.results.common import ElementFieldData

        rdstress_map = self.sif.get_rdstress_map()
        stress_types = [STRESS_MAP[c][0] for c in rdstress_map[irstrs]]
        data = np.array(list(_iter_shell_stress(rv_stresses, rdstress_map, nsp)))
        return ElementFieldData(
            "STRESS",
            int(ires),
            components=stress_types,
            values=data,
            field_pos=ElementFieldData.field_pos.INT,
            int_positions=INT_LOCATIONS[elem_type],
        )

    def get_int_positions(self, rdpoints_res, nlay_i, nsptra_i) -> list:
        iielno_i, ieltyp_i, icoref_i, ijkdim_i = cards.RDPOINTS.get_indices_from_names(
            ["iielno", "ieltyp", "icoref", "ijkdim"]
        )
        iielno = int(rdpoints_res[iielno_i])
        elem = self.mesh.get_elem_by_id(iielno)
        points = [n.p for n in elem.nodes]
        nsptra_len = int(rdpoints_res[nsptra_i] * 9)
        # rmat = np.array(rdpoints_res[-nsptra_len:]).reshape((3, 3))
        nox_data = _get_rdpoints_nox_data(rdpoints_res, nlay_i, nsptra_len)
        p0 = points[0]
        relative_points = np.zeros((len(nox_data), 3))
        for i, v in enumerate(nox_data.values()):
            rel_p = p0 - np.array(v)
            relative_points[i] = rel_p
        # rdielcor_map = sif.get_rdielcor_map()
        # ijkdim = rdpoints_res[ijkdim_i]
        # nok = int(ijkdim / 10000)
        # noj = int((ijkdim % 10000) / 100)
        # noi = int(ijkdim % 100)

        # rdielcor_res = rdielcor_map[int(rdpoints_res[icoref_i])]
        # rdielcor_res_copy = copy.deepcopy(rdielcor_res)
        # gamma = [rdielcor_res_copy.pop() for x in range(0, nok)]
        # beta = [rdielcor_res_copy.pop() for x in range(0, noj)]
        # alpha = [rdielcor_res_copy.pop() for x in range(0, noi)]
        el_type = int(rdpoints_res[ieltyp_i])

        return INT_LOCATIONS[el_type]


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


def _get_rdpoints_nox_data(rdpoints_res, nlay_i, nsptra_len):
    nox_data = rdpoints_res[nlay_i + 1 : -nsptra_len]
    nox_data_clean = dict()
    data_iter = iter(nox_data)
    init_int_point = next(data_iter)
    if init_int_point == -1:
        remaining_el = 0
    else:
        remaining_el = 3

    curr_int_id = int(init_int_point)
    nox_data_clean[curr_int_id] = []

    while True:
        try:
            curr_int_point = next(data_iter)
        except StopIteration:
            break

        if remaining_el > 0:
            nox_data_clean[curr_int_id].append(curr_int_point)
            remaining_el -= 1
        else:
            curr_int_id = int(curr_int_point)
            if curr_int_id == -1:
                remaining_el = 0
            else:
                remaining_el = 3
                nox_data_clean[curr_int_id] = []

    return nox_data_clean


def _iter_shell_stress(rv_stresses: Iterator, rdstress_map, nsp) -> Iterator:
    ires_i, iielno_i, ispalt_i, irstrs_i = cards.RVSTRESS.get_indices_from_names(["ires", "iielno", "ispalt", "irstrs"])
    for rv_stress in rv_stresses:
        iielno = int(rv_stress[iielno_i])
        irstrs = int(rv_stress[irstrs_i])
        rdstress_res = rdstress_map[irstrs]
        data = np.array(rv_stress[irstrs_i + 1 :])
        for i, data_per_int in enumerate(data.reshape((nsp, len(rdstress_res))), start=1):
            yield iielno, i, *data_per_int


def _iter_line_forces(rv_forces: Iterator, rdforces_map, nsp) -> Iterator:
    ires_i, iielno_i, ispalt_i, irforc_i = cards.RVFORCES.get_indices_from_names(["ires", "ielno", "ispalt", "irforc|"])

    for rv_force in rv_forces:
        iielno = int(rv_force[iielno_i])
        irforc = int(rv_force[irforc_i])
        rdstress_res = rdforces_map[irforc]
        data = np.array(rv_force[irforc_i + 1 :])
        for i, data_per_int in enumerate(data.reshape((nsp, len(rdstress_res))), start=1):
            yield iielno, i, *data_per_int
