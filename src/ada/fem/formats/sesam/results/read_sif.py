from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING, Iterator

import numpy as np

from ada.core.utils import Counter
from ada.fem.formats.sesam.common import sesam_eltype_2_general
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.results.sin2sif import convert_sin_to_sif

if TYPE_CHECKING:
    from ada import Material, Section
    from ada.fem.formats.sesam.read.cards import DataCard
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
    25: [
        (0, 0, -0.5),
        (1, 1, -0.5),
        (2, (0.5, 0.5), -0.5),
        (3, 2, -0.5),
        (4, 0, 0.5),
        (5, 1, 0.5),
        (6, (0.5, 0.5), 0.5),
        (7, 2, 0.5),
    ],
    15: [(0, 0), (1, 0.5), (2, 1)],
}
OTHER_CARDS = [
    cards.GUNIVEC,
    cards.TDSECT,
    cards.TDMATER,
    cards.MISOSEL,
    cards.MORSMEL,
    cards.TDSETNAM,
    cards.GSETMEMB,
    cards.TDRESREF,
]
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

    _last_line: str | None = None

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

    def read_gnodes(self, first_line: str):
        for data in self._read_single_line_statements("GNODE", first_line):
            yield data[:2]

    def read_gelmnts(self, first_line: str):
        elno_id, eltyp, members = cards.GELMNT1.get_indices_from_names(["elno", "eltyp", "nids"])
        for data in self._read_multi_line_statements("GELMNT1", first_line):
            yield data[eltyp], data[elno_id], data[members:]

    def read_results(self, result_variable: str, first_line: str) -> tuple:
        for data in self._read_multi_line_statements(result_variable, first_line):
            yield data

    def get_sections(self) -> dict[int, Section]:
        from ada import Section

        sec_map: dict[str, cards.DataCard] = {s.name: s for s in SECTION_CARDS}
        td_sect_map = self.get_tdsect_map()

        sections = dict()
        for sec_name, sec_data in self._sections.items():
            sec_card = sec_map[sec_name]
            sm = cards.SEC_MAP[sec_name]
            sec_type = sm[0]
            keys = [x[0] for x in sm[1]]
            for s in sec_data:
                res = sec_card.get_data_map_from_names(["geono", *keys], s)
                sec_id = int(float(res["geono"]))
                prop_map = {ada_n: res[ses_n] for ses_n, ada_n in sm[1]}
                sec_tdsect = td_sect_map.get(sec_id)
                if sec_tdsect is None:
                    raise ValueError(f"TDSECT is not set for section ID {sec_id}")
                sec_name = sec_tdsect[-1]
                sec = Section(name=sec_name, sec_id=sec_id, sec_type=sec_type, **prop_map)
                sections[sec_id] = sec

        return sections

    def get_sets(self):
        from ada.fem import FemSet

        member_map = self.get_gsetmemb()
        if member_map is None:
            return None
        set_map = self.get_tdsetnam_map()
        istype_i, isorig_i = cards.GSETMEMB.get_indices_from_names(["ISTYPE", "ISORIG"])
        sets = dict()
        for set_id, props in member_map.items():
            eltype = props[istype_i]
            set_type = "nset" if eltype == 1 else "elset"
            set_name = set_map[set_id][-1]
            members = props[isorig_i:]
            sets[set_name] = FemSet(set_name, members, set_type=set_type)
        return sets

    def get_materials(self) -> dict[int, Material]:
        from ada import Material
        from ada.materials.metals import CarbonSteel

        mat_map = {int(x[1]): x[-1] for x in self._other.get("TDMATER", [])}
        isotrop_mats = {int(x[0]): x for x in self._other.get("MISOSEL", [])}
        anisotrop_mats = {int(x[0]): x for x in self._other.get("MORSMEL", [])}

        sm_anisotropic = cards.MAT_MAP["MORSMEL"]
        anisotropic_keys = [s[0] for s in sm_anisotropic]

        sm_isotropic = cards.MAT_MAP["MISOSEL"]
        isotropic_keys = [s[0] for s in sm_isotropic]

        materials = dict()
        for mat_id, mat_data in isotrop_mats.items():
            res = cards.MISOSEL.get_data_map_from_names(isotropic_keys, mat_data)
            mat_name = mat_map[mat_id]
            prop_map = {ada_n: res[ses_n] for ses_n, ada_n in sm_isotropic}
            grade = get_grade(prop_map["sig_y"])
            model = CarbonSteel(grade=grade, **prop_map)
            mat = Material(name=mat_name, mat_id=mat_id, mat_model=model)
            materials[mat_id] = mat

        for mat_id, mat_data in anisotrop_mats.items():
            res = cards.MORSMEL.get_data_map_from_names(anisotropic_keys, mat_data)
            mat_name = mat_map[mat_id]
            prop_map = {ada_n: res[ses_n] for ses_n, ada_n in sm_anisotropic}
            grade = get_grade(prop_map["sig_y"])
            model = CarbonSteel(grade=grade, **prop_map)
            mat = Material(name=mat_name, mat_id=mat_id, mat_model=model)
            materials[mat_id] = mat

        return materials

    def get_vectors(self) -> dict[int, list]:
        res = self._other.get("GUNIVEC")
        if res is None:
            return None
        return {x[0]: x[1:] for x in res}

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
            self._gelref1 = list(self.iter_card(cards.GELREF1, self.file, stripped))

        for other_card in OTHER_CARDS:
            if stripped.startswith(other_card.name):
                if other_card.name in self._other.keys():
                    self._other[other_card.name] += list(self.iter_card(other_card, self.file, stripped))
                else:
                    self._other[other_card.name] = list(self.iter_card(other_card, self.file, stripped))

        for sec_card in SECTION_CARDS:
            if stripped.startswith(sec_card.name):
                self._sections[sec_card.name] = list(self.iter_card(sec_card, self.file, stripped))

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

    def iter_card(self, datacard: DataCard, f: Iterator, curr_line: str):
        curr_elements = [float(x) for x in curr_line.split()[1:]]
        startswith = datacard.name
        n_field = None
        if datacard.components[0] == "nfield":
            n_field = int(curr_elements[0])

        while True:
            num_el = len(curr_elements)
            stripped = next(f).strip()

            if n_field is not None and num_el >= n_field:
                result = datacard.str_to_proper_types(stripped)
                if len(result) == 0:
                    curr_elements += [stripped]
                else:
                    if datacard.name in ("TDSECT", "TDSETNAM", "TDMATER", "TDRESREF"):
                        curr_elements += result
                yield curr_elements
                break

            if n_field is None:
                if stripped.startswith(startswith) is False and datacard.is_numeric(stripped) is False:
                    yield curr_elements
                    break

                if stripped.startswith(startswith):
                    yield curr_elements
                    curr_elements = [float(x) if datacard.is_numeric(x) else x for x in stripped.split()[1:]]
                    continue

            curr_elements += datacard.str_to_proper_types(stripped)

        self._last_line = stripped

    def load(self):
        while True:
            try:
                if self._last_line is not None:
                    line = self._last_line
                    self._last_line = None
                else:
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
        rdpoints = self.get_result(cards.RDPOINTS.name)[0][1]
        return {x[2]: x for x in rdpoints[1:]}

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
        res = self._other.get(cards.TDSECT.name)
        if res is None:
            return None
        return {x[1]: x for x in res}

    def get_tdsetnam_map(self):
        res = self._other.get(cards.TDSETNAM.name)
        if res is None:
            return None
        return {int(x[1]): x for x in res}

    def get_gsetmemb(self):
        res = self._other.get(cards.GSETMEMB.name)
        if res is None:
            return None
        return {int(x[1]): [int(i) for i in x] for x in res}

    def get_rdresref(self):
        res = self.get_result(cards.RDRESREF.name)[0][1]
        if res is None:
            return None
        return {int(x[1]): [int(i) for i in x] for x in res}

    def get_tdresref(self):
        res = self._other.get(cards.TDRESREF.name)
        if res is None:
            return None
        return {int(x[1]): x for x in res}


def read_sif_file(sif_file) -> FEAResult:
    with open(sif_file, "r") as f:
        sif = SifReader(f)
        sif.load()

    s2m = Sif2Mesh(sif)
    fea_results = s2m.convert(sif_file)

    return fea_results


def read_sin_file(sin_file: str | pathlib.Path, overwrite=False) -> FEAResult:
    if isinstance(sin_file, str):
        sin_file = pathlib.Path(sin_file)
    sif_file = sin_file.with_suffix(".SIF")

    if not sif_file.exists() or overwrite:
        convert_sin_to_sif(sin_file)

    return read_sif_file(sif_file)


@dataclass
class Sif2Mesh:
    sif: SifReader

    mesh: Mesh = None

    def convert(self, sif_file) -> FEAResult:
        from ada.fem.results.common import FEAResult, FEATypes

        self.mesh = self.get_sif_mesh()
        results = self.get_sif_results()
        rnames = self.get_result_name_map()

        return FEAResult(
            sif_file.stem,
            FEATypes.SESAM,
            results=results,
            mesh=self.mesh,
            results_file_path=sif_file,
            step_name_map=rnames,
        )

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

        nodes = FemNodes(coords=sif.nodes[:, 1:], identifiers=np.asarray(sif.node_ids[:, 0], dtype=int))
        sorted_elem_data = sorted(sif.elements, key=lambda x: x[0])
        elem_blocks = []
        for eltype, elements in groupby(sorted_elem_data, key=lambda x: x[0]):
            elem_type = int(eltype)
            elem_data = list(elements)
            general_elem_type = sesam_eltype_2_general(elem_type)
            num_nodes = ShapeResolver.get_el_nodes_from_type(general_elem_type)
            elem_identifiers = np.array([x[1] for x in elem_data], dtype=int)
            elem_node_refs = np.array([x[2][:num_nodes] for x in elem_data], dtype=int)

            elem_info = ElementInfo(type=general_elem_type, source_software=FEATypes.SESAM, source_type=elem_type)
            elem_blocks.append(
                ElementBlock(elem_info=elem_info, node_refs=elem_node_refs, identifiers=elem_identifiers)
            )

        sets = self.sif.get_sets()
        sections = self.sif.get_sections()
        materials = self.sif.get_materials()
        vectors = self.sif.get_vectors()
        elem_refs = cards.GELREF1.cast_to_np(["elno", "matno", "geono", "transno"], self.sif.get_gelref())

        return Mesh(
            elements=elem_blocks,
            nodes=nodes,
            sections=sections,
            materials=materials,
            vectors=vectors,
            elem_data=elem_refs,
            sets=sets,
        )

    def get_sif_results(self) -> list[ElementFieldData | NodalFieldData]:
        result_blocks = self.get_nodal_data()
        result_blocks += self.get_field_data()

        return result_blocks

    def get_result_name_map(self):
        tdresref = self.sif.get_tdresref()
        rdresref = self.sif.get_rdresref()
        if tdresref is None:
            # No STEP name is defined
            return {key: key for key, value in rdresref.items()}

        return {key: tdresref[value[1]][-1] for key, value in rdresref.items()}

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
        ires_i, ielno_i, irforc_i = cards.RVFORCES.get_indices_from_names(["ires", "ielno", "irforc|"])
        nsp_i, eltyp_i = cards.RDPOINTS.get_indices_from_names(["nsp", "ieltyp"])

        rdpoints_map = self.sif.get_rdpoints_map()

        def keyfunc(x):
            iielno = x[ielno_i]
            rdpoints_res = rdpoints_map[iielno]
            _nsp = int(rdpoints_res[nsp_i])
            _elem_type = int(rdpoints_res[eltyp_i])
            return x[ires_i], _nsp, _elem_type, x[irforc_i]

        field_results = []

        force_res_name = cards.RVFORCES.name
        for (ires, nsp, elem_type, irforc), rv_forces in groupby(
            sorted(self.sif.get_result(force_res_name)[0][1][1:], key=keyfunc), key=keyfunc
        ):
            if elem_type not in (15,):
                continue
            field_data = self._get_line_field_data(rv_forces, int(ires), int(irforc), elem_type, nsp)
            field_results.append(field_data)

        return field_results

    def _get_line_field_data(self, rv_forces, ires, irforc, elem_type, nsp) -> ElementFieldData:
        from ada.fem.results.common import ElementFieldData

        rdforces_map = self.sif.get_rdforces_map()
        force_types = [FORCE_MAP[c][0] for c in rdforces_map[irforc]]
        data = np.array(list(_iter_line_forces(rv_forces, rdforces_map, nsp)))
        elem_type_ada = sesam_eltype_2_general(elem_type)
        return ElementFieldData(
            "FORCES",
            int(ires),
            components=force_types,
            elem_type=elem_type_ada,
            values=data,
            field_pos=ElementFieldData.field_pos.INT,
            int_positions=INT_LOCATIONS[elem_type],
        )

    def get_field_shell_data(self):
        sif = self.sif
        ires_i, irstrs_i, iielno_i = cards.RVSTRESS.get_indices_from_names(["ires", "irstrs", "iielno"])
        nsp_i, eltyp_i = cards.RDPOINTS.get_indices_from_names(["nsp", "ieltyp"])

        rdpoints_map = sif.get_rdpoints_map()

        def keyfunc(x):
            iielno = x[iielno_i]
            rdpoints_res = rdpoints_map[iielno]
            _nsp = int(rdpoints_res[nsp_i])
            _elem_type = int(rdpoints_res[eltyp_i])
            return x[ires_i], _nsp, _elem_type, x[irstrs_i]

        field_results = []

        for (ires, nsp, elem_type, irstrs), rv_stresses in groupby(
            sorted(sif.get_result(cards.RVSTRESS.name)[0][1][1:], key=keyfunc), key=keyfunc
        ):
            if elem_type not in (25, 24):
                continue

            field_data = self._get_shell_field_data(rv_stresses, ires, irstrs, elem_type, nsp)
            field_results.append(field_data)

        return field_results

    def _get_shell_field_data(self, rv_stresses, ires, irstrs, elem_type: int, nsp) -> ElementFieldData:
        from ada.fem.results.common import ElementFieldData

        rdstress_map = self.sif.get_rdstress_map()
        stress_types = [STRESS_MAP[c][0] for c in rdstress_map[irstrs]]
        data = np.array(list(_iter_shell_stress(rv_stresses, rdstress_map, nsp)))
        elem_type_ada = sesam_eltype_2_general(elem_type)
        return ElementFieldData(
            "STRESS",
            int(ires),
            elem_type=elem_type_ada,
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

        reshaped_data = data.reshape((nsp, len(rdstress_res)))

        for i, data_per_int in enumerate(reshaped_data, start=1):
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


def get_grade(sig_y, tol=1):
    from ada.materials.metals import CarbonSteel

    if abs(sig_y - 420e6) < tol:
        grade = CarbonSteel.TYPES.S420
    elif abs(sig_y - 355e6) < tol:
        grade = CarbonSteel.TYPES.S355
    else:
        grade = "NA"

    return grade
