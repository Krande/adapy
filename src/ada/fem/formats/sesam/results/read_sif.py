from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING, Iterator

import numpy as np

from ada.config import logger
from ada.core.utils import Counter
from ada.fem.formats.sesam.common import sesam_eltype_2_general
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.results.get_version_from_mlg import extract_sestra_version
from ada.fem.formats.sesam.results.sin2sif import convert_sin_to_sif

if TYPE_CHECKING:
    from ada import Material, Section
    from ada.fem.formats.sesam.read.cards import DataCard
    from ada.fem.results.common import ElementFieldData, FEAResult, Mesh, NodalFieldData

FEM_SEC_NAME = Counter(prefix="FS")

# Short, lossy mapping from Sesam GELMNT1 eltyp codes to their
# canonical Sesam names. Used purely for the eltype-histogram info
# log emitted from ``get_sif_mesh`` — gives an unfamiliar SIF an
# at-a-glance breakdown ("oh, the model is 50% BTSS curved beams,
# that explains the spiderweb"). Mirror of ``sesam_el_map`` in
# ``formats/sesam/common.py`` plus the source-software names; kept
# local so the log doesn't drag a dependency on the writer side.
_SESAM_ELTYPE_LABELS = {
    2: "BEAS",
    11: "BMASS",
    15: "BEPS",
    18: "BSPRNGC",
    23: "BTSS",
    24: "FQUS",
    25: "FTRS",
    26: "FTRS6",
    28: "FQUS8",
    31: "ITET10",
    40: "GSPRNGC",
}

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
    # Generic beam properties. Parsed alongside the named-section
    # cards so ``get_sections`` can synthesise a CIRCULAR fallback
    # for elements whose sec_id has only GBEAMG data and no real
    # profile geometry. SESAM's Genie writes these for every beam
    # in a model, so without the fallback ~30% of solid-beam render
    # coverage was being silently dropped.
    cards.GBEAMG,
]
SECTION_CARDS = [cards.GIORH, cards.GBOX, cards.GPIPE]
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
        import math

        from ada import Section
        from ada.sections.categories import BaseTypes

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
                # GPIPE field 'dy' is the outer DIAMETER; ada Section
                # TUBULAR stores the outer RADIUS in ``r``. Halve here
                # rather than wedging arithmetic into SEC_MAP.
                if sec_name == "GPIPE" and "r" in prop_map:
                    prop_map["r"] = float(prop_map["r"]) / 2.0
                sec_tdsect = td_sect_map.get(sec_id)
                # TDSECT is the named-section card; not every profile
                # card has one in the wild (observed: GPIPE entries
                # written by SESAM Genie without a paired TDSECT).
                # Treat the missing-name case as "anonymous section"
                # rather than failing the whole bake — the geometry
                # is still extractable from the profile card.
                if sec_tdsect is None:
                    sec_name_str = f"S{sec_id}"
                else:
                    sec_name_str = sec_tdsect[-1]
                sec = Section(name=sec_name_str, sec_id=sec_id, sec_type=sec_type, **prop_map)
                sections[sec_id] = sec

        # GBEAMG fallback. For sec_ids that only have generic beam
        # properties (area + Iy/Iz/...) and no profile card, synthesise
        # a CIRCULAR section with radius matching the area so beam-
        # as-solid render fills these in instead of dropping them.
        # The visual is a round bar of the right cross-sectional
        # area — accurate at-a-glance but doesn't reflect the real
        # profile shape. Marked as such in ``name`` so users browsing
        # the tree see it isn't a real profile.
        gbeamg_rows = self._other.get("GBEAMG", []) or []
        for s in gbeamg_rows:
            res = cards.GBEAMG.get_data_map_from_names(
                ["geono", "area"], s,
            )
            try:
                sec_id = int(float(res["geono"]))
                area = float(res["area"])
            except (KeyError, TypeError, ValueError):
                continue
            if sec_id in sections:
                continue
            if not (area > 0):
                # Zero / negative / NaN — can't back out a radius.
                # Skip; the beam falls into ``no-section`` and we
                # surface it in the bake's skip-reasons tally.
                continue
            r = math.sqrt(area / math.pi)
            sec_tdsect = td_sect_map.get(sec_id)
            sec_name_str = (
                sec_tdsect[-1] if sec_tdsect is not None else f"GBEAMG{sec_id}"
            )
            sections[sec_id] = Section(
                name=sec_name_str,
                sec_id=sec_id,
                sec_type=BaseTypes.CIRCULAR,
                r=r,
            )

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
        from ada.materials.metals import CarbonSteel, Metal

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
            # MISOSEL is generic linear-elastic isotropic — Sesam doesn't
            # tell us the material is steel. Bucket sig_y against the two
            # canonical steel grades; if it matches, emit a CarbonSteel
            # so downstream EC3 / steel-specific helpers light up.
            # Otherwise (S275 / S460 / S690 / aluminium / anything else),
            # emit a plain Metal so we don't fabricate a steel grade we
            # don't know is correct — and so CarbonSteel.GRADES["NA"]
            # doesn't crash mid-conversion.
            grade = get_grade(prop_map["sig_y"])
            if grade in CarbonSteel.GRADES:
                model = CarbonSteel(grade=grade, **prop_map)
            else:
                model = _build_metal(prop_map)
            mat = Material(name=mat_name, mat_id=mat_id, mat_model=model)
            materials[mat_id] = mat

        for mat_id, mat_data in anisotrop_mats.items():
            res = cards.MORSMEL.get_data_map_from_names(anisotropic_keys, mat_data)
            mat_name = mat_map[mat_id]
            prop_map = {ada_n: res[ses_n] for ses_n, ada_n in sm_anisotropic}
            # MORSMEL is the orthotropic counterpart and carries no
            # yield stress at all. Calling get_grade() here used to
            # KeyError on the missing "sig_y" key — emit a plain Metal
            # with zero sig_y/sig_u (sentinel "unknown yield") instead.
            model = _build_metal(prop_map)
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

    # Card-name → DataCard dispatch maps. Built lazily on first
    # ``eval_flags`` call so the dataclass default-factory dance
    # stays simple. Replacing the original ``startswith`` chain
    # (one comparison per card per line × ~15 cards × 1.1M lines)
    # with O(1) dict lookups is the hot-path win — SIF profiling
    # showed ``eval_flags`` was 38 s of a 67 s SIF→GLB conversion,
    # dominated by chained ``str.startswith`` calls on every line.
    _other_map: dict | None = None
    _section_map: dict | None = None
    _result_map: dict | None = None

    def _build_dispatch_maps(self) -> None:
        self._other_map = {c.name: c for c in OTHER_CARDS}
        self._section_map = {c.name: c for c in SECTION_CARDS}
        self._result_map = {c.name: c for c in RESULT_CARDS}

    def eval_flags(self, line: str):
        if self._other_map is None:
            self._build_dispatch_maps()

        stripped = line.strip()
        if not stripped:
            return

        # Continuation lines (numeric or signed-numeric prefix) don't
        # carry a card name; they're consumed by the parent
        # ``iter_card`` call. The first-block elif in the original
        # used ``isnumeric()`` for this same gate.
        first = stripped[0]
        if first.isdigit() or first == "-":
            return

        # Single tokenisation: take everything up to the first space
        # as the card name. SIF cards always start at column 0 with
        # the card name immediately followed by whitespace.
        space_idx = stripped.find(" ")
        token = stripped if space_idx < 0 else stripped[:space_idx]

        is_skipped_flag = True  # default; cleared by the GCOORD /
                                # GNODE / GELMNT1 / RESULT_CARDS
                                # branches to match original semantics

        # First-block exclusive cards (Nodes, Elements).
        if token == cards.GCOORD.name:
            self.nodes = np.array(list(self.read_gcoords(stripped)))
            is_skipped_flag = False
        elif token == cards.GNODE.name:
            self.node_ids = np.array(list(self.read_gnodes(stripped)))
            is_skipped_flag = False
        elif token == cards.GELMNT1.name:
            self.elements = list(self.read_gelmnts(stripped))
            is_skipped_flag = False

        # Sections (GELREF1 is independent in the original).
        if token == cards.GELREF1.name:
            self._gelref1 = list(self.iter_card(cards.GELREF1, self.file, stripped))

        other_card = self._other_map.get(token)
        if other_card is not None:
            new_rows = list(self.iter_card(other_card, self.file, stripped))
            existing = self._other.get(token)
            if existing is None:
                self._other[token] = new_rows
            else:
                existing.extend(new_rows)

        sec_card = self._section_map.get(token)
        if sec_card is not None:
            # SIF files emit section cards in non-contiguous blocks —
            # GIORH records can appear, then a GBEAMG / TDSECT block,
            # then more GIORH later in the file. Each ``iter_card``
            # call only consumes the *current* contiguous block, so
            # we accumulate across encounters rather than overwrite.
            new_rows = list(self.iter_card(sec_card, self.file, stripped))
            existing = self._sections.get(token)
            if existing is None:
                self._sections[token] = new_rows
            else:
                existing.extend(new_rows)

        res_card = self._result_map.get(token)
        if res_card is not None:
            rows = list(self.read_results(token, stripped))
            # SIF result cards (RVNODDIS / RVSTRESS / RVFORCES / etc.)
            # commonly hold 100k–10M rows. Each row as a Python
            # ``list[float]`` carries ~136 bytes of list overhead +
            # 24 bytes per float — ~376 B / row total. A typical
            # Sesam result file's 1.8 M rows blow that out to
            # ~675 MB just in list-of-list overhead during parse,
            # then again in the rebuilt ndarray during conversion.
            #
            # Convert to a contiguous float64 ndarray here as soon
            # as we have all rows, so the per-row lists are
            # released before the next card runs. Each row drops
            # from ~376 B to 80 B (one ndarray row), and downstream
            # consumers iterate / index the same way they did
            # against list-of-lists.
            #
            # Only convert when every row has the same width AND
            # contains only numeric scalars — some cards (e.g.
            # those with a trailing name string) are intentionally
            # heterogeneous and stay as lists.
            if rows:
                width = len(rows[0])
                uniform = all(
                    isinstance(r, list) and len(r) == width
                    and not any(isinstance(v, str) for v in r)
                    for r in rows
                )
                if uniform:
                    try:
                        rows = np.asarray(rows, dtype=np.float64)
                    except (ValueError, TypeError):
                        pass  # fall back to list-of-lists
            self.results.append((token, rows))
            is_skipped_flag = False

        if is_skipped_flag:
            if token not in self.skipped_flags:
                self.skipped_flags.append(token)

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


def read_sif_file(sif_file: str | pathlib.Path) -> FEAResult:
    # Sif2Mesh.convert() calls sif_file.parent to find sibling
    # SESTRA.MLG / SESTRA.LIS files; coerce here so callers passing a
    # plain string (e.g. the legacy converter pipeline's
    # `read_sif_file(str(src_path))`) don't trip an AttributeError.
    sif_file = pathlib.Path(sif_file)
    with open(sif_file, "r") as f:
        sif = SifReader(f)
        sif.load()

    s2m = Sif2Mesh(sif)
    fea_results = s2m.convert(sif_file)

    return fea_results


def read_sin_file(sin_file: str | pathlib.Path, overwrite=False) -> FEAResult:
    """Read a Sesam SIN / SIF result file → :class:`FEAResult`.

    Despite the name, this is the Sesam-side post-processor entry
    point (``SesamSetup.default_post_processor``) — ``from_fem_res``
    funnels both ``.sif`` (text) and ``.sin`` (Norsam binary) here.

    Routes by extension:

    * ``.sif`` → :func:`read_sif_file` (existing text-parser path).
    * ``.sin`` → :func:`ada.fem.formats.sesam.results.read_sin.read_sin_file`
      — pure-Python direct path: SIN bytes → :class:`SinReader` (mirrors
      SifReader's internal state) → :class:`Sif2Mesh` → FEAResult.
      No Prepost.exe shell-out, no SIF text intermediate, no .NET
      dependency. A standalone SIN-to-SIF text path lives in
      :mod:`sin_to_sif` for debugging / interop only — not on the
      streaming-bake hot path.

    ``overwrite=True`` (legacy callers) materialises a SIF file next
    to the SIN and routes through :func:`read_sif_file` for back-compat.
    """
    sin_file = pathlib.Path(sin_file)
    if sin_file.suffix.lower() == ".sif":
        return read_sif_file(sin_file)
    if overwrite:
        from ada.fem.formats.sesam.results.sin_to_sif import convert_sin_to_sif_file

        sif_path = convert_sin_to_sif_file(sin_file)
        return read_sif_file(sif_path)
    from ada.fem.formats.sesam.results.read_sin import read_sin_file as _native_read_sin

    return _native_read_sin(sin_file)


@dataclass
class Sif2Mesh:
    sif: SifReader

    mesh: Mesh = None

    def convert(self, sif_file) -> FEAResult:
        from ada.fem.formats.sesam.results._results import get_eigen_data
        from ada.fem.results.common import FEAResult, FEATypes, NodalFieldData

        self.mesh = self.get_sif_mesh()
        results = self.get_sif_results()
        rnames = self.get_result_name_map()
        mlg_file = sif_file.parent / "SESTRA.MLG"
        lis_file = sif_file.parent / "SESTRA.LIS"

        if lis_file.exists():
            try:
                eig_data = get_eigen_data(lis_file)
                for result in results:
                    if not isinstance(result, NodalFieldData):
                        continue
                    eig_freq = eig_data.get_eigenmode(result.step)
                    if eig_freq is not None:
                        result.eigen_freq = eig_freq.f_hz
                        result.eigen_value = eig_freq.eigenvalue
            except Exception as e:
                logger.info("Unable to extract eigen data from LIS file. Error: %s", e)

        software_version = "N/A"
        if mlg_file.exists():
            software_version = extract_sestra_version(mlg_file)

        return FEAResult(
            sif_file.stem,
            FEATypes.SESAM,
            results=results,
            mesh=self.mesh,
            results_file_path=sif_file,
            step_name_map=rnames,
            software_version=software_version,
        )

    def get_sif_mesh(self) -> Mesh:
        from ada.fem.results.common import (
            ElementBlock,
            ElementInfo,
            FEATypes,
            FemNodes,
            Mesh,
        )
        from ada.fem.shapes.definitions import LineShapes, ShapeResolver
        from ada.fem.shapes.mesh_types import gmsh_to_meshio_ordering

        sif = self.sif

        nodes = FemNodes(coords=sif.nodes[:, 1:], identifiers=np.asarray(sif.node_ids[:, 0], dtype=int))
        sorted_elem_data = sorted(sif.elements, key=lambda x: x[0])
        elem_blocks = []
        # Per-element-type histogram, logged below. Helps diagnose
        # vis bugs (spiderweb / floating segments) without the source
        # file — the user can grep the conversion log for the line and
        # tell us what their model actually contains.
        eltype_counts: dict[int, int] = {}
        for eltype, elements in groupby(sorted_elem_data, key=lambda x: x[0]):
            elem_type = int(eltype)
            elem_data = list(elements)
            general_elem_type = sesam_eltype_2_general(elem_type)
            num_nodes = ShapeResolver.get_el_nodes_from_type(general_elem_type)
            elem_identifiers = np.array([x[1] for x in elem_data], dtype=int)
            elem_node_refs = np.array([x[2][:num_nodes] for x in elem_data], dtype=int)

            # Node-ordering reconciliation. Sesam's BTSS (eltyp 23 →
            # LINE3) writes the three nodes as (end1, end2, mid) —
            # the GMSH convention. adapy's ElemShape machinery and
            # the line_edges table both assume Abaqus ordering
            # (end1, mid, end2): without the permutation,
            # ``line_edges[LINE3] = [[0, 2]]`` resolves to "end1 →
            # mid" and every curved beam visualises as half a
            # segment, producing the spiderweb effect on ship-FE
            # models. The same gmsh→meshio permutation is already
            # codified in mesh_types.gmsh_to_meshio_ordering — reuse
            # it here rather than hardcoding the indices a second
            # time.
            if general_elem_type is LineShapes.LINE3:
                perm = gmsh_to_meshio_ordering.get(LineShapes.LINE3)
                if perm is not None:
                    elem_node_refs = elem_node_refs[:, perm]

            elem_info = ElementInfo(type=general_elem_type, source_software=FEATypes.SESAM, source_type=elem_type)
            elem_blocks.append(
                ElementBlock(elem_info=elem_info, node_refs=elem_node_refs, identifiers=elem_identifiers)
            )
            eltype_counts[elem_type] = elem_identifiers.size

        if eltype_counts:
            # INFO so it shows up in the worker logs without needing a
            # debug-level switch — diagnosing a "spiderweb" vis bug is
            # part of normal triage for unfamiliar SIF files. Format
            # tries to be greppable: "sesam-eltype histogram: {...}".
            summary = ", ".join(
                f"{etyp}({_SESAM_ELTYPE_LABELS.get(etyp, '?')})={n}"
                for etyp, n in sorted(eltype_counts.items())
            )
            logger.info("sesam-eltype histogram: %s", summary)

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
        # No element result-point geometry → no per-element force
        # visualisation we can construct. Some eigen decks ship
        # RDPOINTS as an empty type-block in every super-element;
        # the nodal field path still works and is the primary bake
        # output.
        if not rdpoints_map:
            return []

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
    from ada.fem.results.field_data import NodalFieldType

    comps = "U1|", "U2|", "U3|", "U4|", "U5|", "U6|"
    indices = cards.RVNODDIS.get_indices_from_names(["inod", *comps])
    nid = indices[0]
    start = indices[1]
    stop = indices[-1] + 1
    results = []
    for ires, data in groupby(res[1:], key=lambda x: x[1]):
        field_data = np.asarray([(x[nid], *x[start:stop]) for x in data], dtype=float)

        fd = NodalFieldData(
            name=cards.RVNODDIS.name,
            step=int(ires),
            components=[x.replace("|", "") for x in comps],
            values=field_data,
            field_type=NodalFieldType.DISP,
        )
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


def _build_metal(prop_map: dict) -> "Metal":
    """Build a plain ``Metal`` from a MISOSEL / MORSMEL prop dict.

    Used when the SIF material doesn't match a canonical steel grade
    (where we'd promote it to ``CarbonSteel``) or when the source card
    is orthotropic and carries no yield info at all. ``Metal``
    requires sig_y / sig_u positionally; for the orthotropic case
    those aren't on the card, so we fall back to 0.0 — a sentinel
    meaning "yield unknown" rather than a real value the downstream
    code should believe.
    """
    from ada.materials.metals import Metal

    sig_y = prop_map.get("sig_y", 0.0)
    sig_u = prop_map.get("sig_u", sig_y)
    return Metal(
        E=prop_map["E"],
        rho=prop_map["rho"],
        sig_y=sig_y,
        sig_u=sig_u,
        v=prop_map["v"],
        zeta=prop_map["zeta"],
        alpha=prop_map["alpha"],
    )
