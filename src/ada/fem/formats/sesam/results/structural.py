"""A Sesam SIN as a format-neutral FEA result: role fields, model data, and its cases read one at a time.

:func:`open_sin_result` is the SIN branch of ``ada.from_fem_res(path, lazy=True)``. The result it returns
holds the mesh and the model data a structural check reads (``Mesh.thickness``, ``element_frames``,
``result_points``, ``section_kinds``, ``concept_names``, ``restrained_nodes``, ``eccentricities``) and
``FEAResult.cases``, which reads the stored cases one at a time as the role fields of
:class:`~ada.fem.results.field_data.FieldRole`. Sesam's own convention is adapy's canonical one, so a SIN's
``RVSTRESS`` / ``RVFORCES`` / ``RVNODDIS`` are those fields as they are.

Numbers: a Sesam deck numbers every node and element twice (GNODE, GELMNT1). The EXTERNAL number is the
one people read (GeniE, Xtract, the viewer); the INTERNAL one, 1..n, is what every other record refers by.
Everything here is in the external numbers: the mesh, the model data and every case's fields.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

import numpy as np

from ada.fem.results.cases import ResultCases
from ada.fem.results.field_data import FieldRole

#: The raw blocks a case is read as, and their roles.
RAW_ROLES = {
    "STRESS": FieldRole.SHELL_STRESS,
    "FORCES": FieldRole.BEAM_FORCES,
    "RVNODDIS": FieldRole.NODAL_DISPLACEMENT,
}
_FIELD_OF_ROLE = {role: name for name, role in RAW_ROLES.items()}

#: ``Mesh.section_kinds`` by Sesam profile card (Input Interface File section 7.3).
SECTION_KIND_OF_CARD = {
    "GIORH": "i",
    "GIORHR": "i",
    "GUSYI": "i",
    "GTONP": "i",
    "GLSEC": "angle",
    "GLSECR": "angle",
    "GBARM": "bar",
    "GBOX": "box",
    "GPGBOX": "box",
    "GDOBO": "box",
    "GPIPE": "pipe",
    "GCHAN": "channel",
    "GCHANR": "channel",
    "GPGDOW": "general",
}


# --------------------------------------------------------------------------------------------- numbering
@dataclasses.dataclass(frozen=True)
class Numbering:
    """A deck's ``{internal: external}`` node and element maps; None where the two agree."""

    nodes: Mapping[int, int] | None = None
    elements: Mapping[int, int] | None = None
    _cache: dict = dataclasses.field(default_factory=dict, compare=False, repr=False)

    @property
    def identity(self) -> bool:
        return self.nodes is None and self.elements is None

    def shown_element(self, element: int) -> int:
        return int(element) if self.elements is None else int(self.elements.get(int(element), int(element)))

    def shown_node(self, node: int) -> int:
        return int(node) if self.nodes is None else int(self.nodes.get(int(node), int(node)))

    def internal_elements(self, elements: Iterable[int]) -> set[int]:
        if self.elements is None:
            return {int(e) for e in elements}
        inverse = self._cache.get("inverse")
        if inverse is None:
            inverse = self._cache["inverse"] = {int(v): int(k) for k, v in self.elements.items()}
        return {int(inverse.get(int(e), int(e))) for e in elements}

    def lookup(self, kind: str, ids: Any) -> np.ndarray:
        """``ids`` with every ``kind`` (``"nodes"``/``"elements"``) number shown, others kept."""
        mapping = self.nodes if kind == "nodes" else self.elements
        arr = np.asarray(ids)
        if mapping is None or arr.size == 0:
            return arr
        table = self._cache.get(kind)
        if table is None:
            keys = np.fromiter(mapping.keys(), dtype=np.int64, count=len(mapping))
            values = np.fromiter(mapping.values(), dtype=np.int64, count=len(mapping))
            order = np.argsort(keys, kind="stable")
            table = self._cache[kind] = (keys[order], values[order])
        keys, values = table
        flat = arr.reshape(-1).astype(np.int64)
        at = np.clip(np.searchsorted(keys, flat), 0, keys.size - 1)
        out = np.where(keys[at] == flat, values[at], flat)
        return out.reshape(arr.shape).astype(arr.dtype, copy=False)


def numbering_of_mesh(mesh: Any) -> Numbering:
    """The numbering an adapy mesh read from a SIN carries (``FemNodes.labels``, ``ElementBlock.labels``)."""
    nodes_labelled = getattr(getattr(mesh, "nodes", None), "labels", None) is not None
    elements_labelled = any(getattr(b, "labels", None) is not None for b in getattr(mesh, "elements", None) or [])
    if not nodes_labelled and not elements_labelled:
        return Numbering()
    nodes = {int(i): int(x) for i, x in zip(mesh.nodes.identifiers, mesh.nodes.labels)} if nodes_labelled else None
    elements = None
    if elements_labelled:
        elements = {}
        for block in mesh.elements:
            shown = getattr(block, "labels", None)
            elements.update(
                (int(i), int(x)) for i, x in zip(block.identifiers, block.identifiers if shown is None else shown)
            )
    return Numbering(nodes=nodes, elements=elements)


def shown_mesh(mesh: Any, numbering: Numbering) -> Any:
    """``mesh`` keyed by the external numbers (a copy); ``mesh`` itself when they agree."""
    if numbering.identity:
        return mesh
    from ada.fem import FemSet
    from ada.fem.results.common import ElementBlock, FemNodes

    nodes = FemNodes(coords=mesh.nodes.coords, identifiers=numbering.lookup("nodes", mesh.nodes.identifiers))
    blocks = []
    for block in mesh.elements:
        refs = block.node_refs
        as_indices = getattr(block, "node_refs_are_indices", False)
        if not as_indices:
            refs = numbering.lookup("nodes", refs)
        blocks.append(
            ElementBlock(
                block.elem_info,
                refs,
                numbering.lookup("elements", block.identifiers),
                node_refs_are_indices=as_indices,
            )
        )
    elem_data = mesh.elem_data
    if elem_data is not None and numbering.elements is not None:
        elem_data = np.array(elem_data, copy=True)
        elem_data[:, 0] = numbering.lookup("elements", elem_data[:, 0])
    sets = mesh.sets
    if sets:
        out_sets = {}
        for name, fs in sets.items():
            ids = getattr(fs, "_member_ids", None)
            kind = str(getattr(fs, "type", "elset")).lower()
            mapping = numbering.nodes if kind == "nset" else numbering.elements
            out_sets[name] = (
                fs
                if not ids or mapping is None
                else FemSet(fs.name, [int(mapping.get(int(i), int(i))) for i in ids], set_type=kind)
            )
        sets = out_sets
    ecc = mesh.eccentricities
    if ecc:
        ecc = {numbering.shown_element(k): v for k, v in ecc.items()}
    return dataclasses.replace(mesh, elements=blocks, nodes=nodes, elem_data=elem_data, sets=sets, eccentricities=ecc)


def shown_fields(fields: list, numbering: Numbering) -> None:
    """Every field's leading id column in external numbers, in place (copies of the values)."""
    if numbering.identity:
        return
    from ada.fem.results.field_data import NodalFieldData

    for field in fields:
        values = getattr(field, "values", None)
        if values is None:
            continue
        values = np.asarray(values)
        if values.ndim != 2 or values.shape[0] == 0:
            continue
        kind = "nodes" if isinstance(field, NodalFieldData) else "elements"
        if (numbering.nodes if kind == "nodes" else numbering.elements) is None:
            continue
        shown = np.array(values, copy=True)
        shown[:, 0] = numbering.lookup(kind, values[:, 0].astype(np.int64))
        field.values = shown


# ------------------------------------------------------------------------------------------- model data
def _blocks(sin) -> Mapping:
    return getattr(sin, "type_blocks", None) or {}


def _records(sin, name: str):
    return sin.iter_records(name) if name in _blocks(sin) else ()


def read_thickness_by_geono(sin) -> dict[int, float]:
    """GELTH: ``{geono: thickness}``."""
    return {int(r[0]): float(r[1]) for r in _records(sin, "GELTH") if len(r) >= 2}


def read_section_kinds(sin) -> dict[int, str]:
    """``{geono: the profile card it has}``, first card wins (``SECTION_KIND_OF_CARD``'s keys)."""
    out: dict[int, str] = {}
    for card in SECTION_KIND_OF_CARD:
        for rec in _records(sin, card):
            if len(rec):
                out.setdefault(int(rec[0]), card)
    return out


def read_bar_sections(sin) -> dict[int, tuple[float, float, float]]:
    """GBARM: ``{geono: (HZ, BT, BB)}`` -- a solid bar's height and its widths at top and bottom."""
    return {int(r[0]): (float(r[1]), float(r[2]), float(r[3])) for r in _records(sin, "GBARM") if len(r) >= 4}


def read_result_points(sin, numbering: Numbering) -> tuple[dict[int, dict[int, np.ndarray]], dict[int, np.ndarray]]:
    """RDPOINTS: ``({element: {point: xyz}}, {element: 3x3 frame})`` in external numbers.

    A record is ``ISPALT IIELNO ICOREF IELTYP NSP IJKDIM NSPTRA NLAY``, then ``point, x, y, z`` groups
    (with ``-1`` separators) and ``NSPTRA`` 3x3 transformations, rows the local axes in global ones.
    """
    points: dict[int, dict[int, np.ndarray]] = {}
    frames: dict[int, np.ndarray] = {}
    for rec in _records(sin, "RDPOINTS"):
        if len(rec) < 9:
            continue
        elno = numbering.shown_element(int(rec[1]))
        n_frames = int(rec[6])
        width = n_frames * 9
        bulk = rec[8:-width] if width else rec[8:]
        if width:
            frame = np.array(rec[-width:], dtype=float).reshape((n_frames, 3, 3))
            if len(frame):
                frames[elno] = frame[0]
        found: dict[int, np.ndarray] = {}
        i = 0
        while i < len(bulk):
            pid = int(bulk[i])
            i += 1
            if pid == -1:
                continue
            if i + 2 >= len(bulk):
                break
            found[pid] = np.array((float(bulk[i]), float(bulk[i + 1]), float(bulk[i + 2])), dtype=float)
            i += 3
        if found:
            points[elno] = found
    return points, frames


def read_concept_names(sin, numbering: Numbering) -> dict[int, str]:
    """TDSCONC/SCONCEPT/SCONMESH: ``{element: the name of the structural concept it meshes}``."""
    if not all(name in _blocks(sin) for name in ("TDSCONC", "SCONCEPT", "SCONMESH")):
        return {}
    names = {int(prefix[0]): text for prefix, text in sin.iter_text_records("TDSCONC") if prefix and text}
    concept_to_mesh = {int(r[0]): int(r[-1]) for r in sin.iter_records("SCONCEPT") if len(r) >= 7 and int(r[1]) == 7}
    mesh_to_elements = {int(r[0]): tuple(int(e) for e in r[4:]) for r in sin.iter_records("SCONMESH") if len(r) >= 5}
    out: dict[int, str] = {}
    for concept, mesh_id in concept_to_mesh.items():
        name = names.get(concept)
        if not name:
            continue
        for element in mesh_to_elements.get(mesh_id, ()):
            out[numbering.shown_element(element)] = name
    return out


def read_restrained_nodes(sin, numbering: Numbering) -> frozenset[int]:
    """Nodes with any DOF not free in BNBCD (fixed, prescribed, dependent, retained) or at either end of a
    BLDEP rigid link, in external numbers."""
    out: set[int] = set()
    for rec in _records(sin, "BNBCD"):
        if len(rec) < 3:
            continue
        ndof = int(rec[1])
        if any(int(round(float(code))) != 0 for code in rec[2 : 2 + ndof]):
            out.add(int(rec[0]))
    for rec in _records(sin, "BLDEP"):
        if len(rec) >= 2:
            out.add(int(rec[0]))
            out.add(int(rec[1]))
    return frozenset(numbering.shown_node(n) for n in out)


def read_load_case_map(sin) -> tuple[dict[int, int], dict[int, tuple[tuple[int, float, float], ...]]]:
    """RBLODCMB: ``({result case: load case}, {result case: its terms, where not one load case at factor 1})``."""
    mapping: dict[int, int] = {}
    irregular: dict[int, tuple[tuple[int, float, float], ...]] = {}
    for rec in _records(sin, "RBLODCMB"):
        if len(rec) < 3:
            continue
        ires, nblc = int(round(rec[0])), int(round(rec[2]))
        terms = tuple(
            (int(round(rec[3 + 3 * i])), float(rec[4 + 3 * i]), float(rec[5 + 3 * i]))
            for i in range(nblc)
            if 5 + 3 * i < len(rec)
        )
        if len(terms) == 1 and terms[0][1] == 1.0 and terms[0][2] == 0.0:
            mapping[ires] = terms[0][0]
        else:
            irregular[ires] = terms
    return mapping, irregular


def read_shell_pressures(sin, numbering: Numbering) -> dict[int, dict[int, float]]:
    """BEUSLO: the mean signed shell pressure ``{load case: {element: p}}`` (``-case`` the imaginary part of a
    phased one), positive along the element's local z (Sesam's sign).

    The records of one element and load case add, whatever their SIDE or LAYER: an element carries every element
    load of its case (two pressures on one plate in a case, as a combination of two cases loading it is written).
    Measured, Sestra V11.3: two BEUSLO records of 400 and 600 Pa on every element of the strip in one case gave the
    reaction of 1000 Pa and the mid-span deflection of one 1000 Pa record (``tests/fem/test_sesam_pressure_load.py``).
    SIDE and LAYER do not change what Sestra applies -- it computes a BEUSLO in the element's mid-plane and returns
    the identical result for SIDE 1, 2 and 3, and ignores LAYER -- the intensity's sign carries the direction
    (:mod:`ada.fem.formats.sesam.write.write_loads`). The last record used to replace the others, so a plate under
    50 and 30 kPa in one case read back 30 kPa; and a Sestra SIN keeps the second record of an element and case
    chained behind the first, which the reader did not follow, so the same plate read back 50 kPa
    (:meth:`~.sin_reader.SinFile.iter_keyed_records`).

    Only LOTYP 1 records are pressures: a LOTYP 2 record is a traction vector, three components per node (GeniE
    writes those, e.g. ``files/fem_files/sesam/genie_loads_all_kinds_T1.FEM``), whose mean is no pressure; it is
    left out and logged, as the deck reader refuses it (``sesam.read.read_loads``)."""
    from ada.config import logger

    out: dict[int, dict[int, float]] = {}
    skipped: dict[int, int] = {}

    def add(case: int, element: int, values: list[float]) -> None:
        table = out.setdefault(case, {})
        table[element] = table.get(element, 0.0) + float(np.mean(values))

    keyed = getattr(sin, "iter_keyed_records", None)
    # Sestra keeps a second record of one element and case behind the first (sin_reader.iter_keyed_records):
    # key LLC (data word 0) and ELNO (data word 4).
    records = keyed("BEUSLO", (0, 4)) if keyed is not None and "BEUSLO" in _blocks(sin) else _records(sin, "BEUSLO")
    for rec in records:
        if len(rec) < 9:
            continue
        lotyp = int(round(rec[1]))
        if lotyp != 1:
            skipped[lotyp] = skipped.get(lotyp, 0) + 1
            continue
        case = int(rec[0])
        element = numbering.shown_element(int(rec[4]))
        n_values = int(rec[5]) if len(rec) > 5 and rec[5] > 0 else len(rec) - 8
        values = [float(x) for x in rec[8 : 8 + n_values]]
        if not values:
            continue
        add(case, element, values)
        if case > 0 and int(rec[2]) != 0 and int(rec[5]) > 0:
            imaginary = [float(x) for x in rec[8 + n_values : 8 + 2 * n_values]]
            if len(imaginary) == n_values:
                add(-case, element, imaginary)
    for lotyp, n in sorted(skipped.items()):
        logger.warning(f"{n} BEUSLO record(s) of LOTYP {lotyp} are no shell pressure (LOTYP 1); left out of the pressures")
    return out


def pressures_by_result_case(by_load_case: dict, load_case_of: Mapping[int, int]) -> dict:
    """``by_load_case`` re-keyed by result case through ``load_case_of``; a result case it does not name is
    looked up by its own number (the two coincide on a SIN solved in one run)."""
    if all(ires == llc for ires, llc in load_case_of.items()):
        return by_load_case
    out = {case: table for case, table in by_load_case.items() if abs(case) not in load_case_of}
    for ires, llc in load_case_of.items():
        for sign in (1, -1):
            table = by_load_case.get(sign * llc)
            if table is not None:
                out[sign * ires] = table
    return out


def read_calculation_types(sin) -> dict[int, int]:
    """RDRESREF: ``{result case: ICALTY}``."""
    return {int(round(r[0])): int(round(r[3])) for r in _records(sin, "RDRESREF") if len(r) >= 5}


def element_thickness(mesh: Any, thickness_by_geono: Mapping[int, float]) -> dict[int, float]:
    """``{shell element: thickness}`` through ``elem_data``'s geono column."""
    from ada.fem.shapes.definitions import ShellShapes

    if mesh.elem_data is None:
        return {}
    shells = {
        int(e) for b in mesh.elements if isinstance(b.elem_info.type, ShellShapes) for e in np.asarray(b.identifiers)
    }
    out: dict[int, float] = {}
    for el, _mat, geono, *_ in np.asarray(mesh.elem_data):
        t = thickness_by_geono.get(int(geono))
        if t is not None and int(el) in shells:
            out[int(el)] = float(t)
    return out


# ----------------------------------------------------------------------------------------------- reader
def _open(path: pathlib.Path, super_element: Any):
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    return open_sin(path) if super_element is None else open_sin(path, super_element=super_element)


def open_sin_result(path: str | os.PathLike, *, super_element: Any = None):
    """The SIN at ``path`` as an FEA result whose cases are read one at a time (see the module).

    ``super_element``: on a superelement assembly, which one (``sin_reader.open_sin``).
    """
    from ada.fem.formats.sesam.results import read_sin
    from ada.fem.results.common import FEAResult, FEATypes

    path = pathlib.Path(path)
    # The mesh alone (step 1's, without its results); the stream closes the file it reads.
    with read_sin.SinStreamReader(_open(path, super_element)) as stream:
        raw_mesh = stream._load_step(1, cards=set(), requested_fields=set()).mesh
    sin = _open(path, super_element)
    try:
        numbering = numbering_of_mesh(raw_mesh)
        mesh = shown_mesh(raw_mesh, numbering)
        points, frames = read_result_points(sin, numbering)
        bars = read_bar_sections(sin)
        mesh.thickness = element_thickness(mesh, read_thickness_by_geono(sin))
        mesh.result_points = points
        mesh.element_frames = frames
        mesh.section_kinds = {g: SECTION_KIND_OF_CARD[c] for g, c in read_section_kinds(sin).items()}
        mesh.concept_names = read_concept_names(sin, numbering)
        mesh.restrained_nodes = read_restrained_nodes(sin, numbering)
        _add_bar_sections(mesh, bars, sin)

        stored = sorted(read_sin.stored_result_cases(sin))
        load_case_of, _irregular = read_load_case_map(sin)
        cases = ResultCases(
            stored=stored,
            loader=_Loader(path, super_element, numbering, mesh),
            names=read_sin.read_result_names(sin),
            combinations=read_sin.read_result_combination_terms(sin),
            complex_cases=read_sin.read_complex_result_cases(sin),
            calculation_types=read_calculation_types(sin),
            load_case_of=load_case_of,
            pressures=pressures_by_result_case(read_shell_pressures(sin, numbering), load_case_of),
        )
    finally:
        sin.close()
    return FEAResult(
        path.stem,
        software=FEATypes.SESAM,
        results=[],
        mesh=mesh,
        results_file_path=path,
        step_name_map=dict(cases.names),
        cases=cases,
        analysis_kind="static",
    )


def _add_bar_sections(mesh: Any, bars: dict[int, tuple[float, float, float]], sin) -> None:
    """GBARM sections, which the SIN mesh reader does not build, as ``Section(FLATBAR)``: ``h`` = HZ,
    ``w_top`` = BT, ``w_btn`` = BB."""
    if not bars:
        return
    from ada import Section
    from ada.sections.categories import BaseTypes

    names = (
        {int(prefix[0]): text for prefix, text in sin.iter_text_records("TDSECT") if prefix and text}
        if "TDSECT" in _blocks(sin)
        else {}
    )
    sections = dict(mesh.sections or {})
    for geono, (hz, bt, bb) in bars.items():
        if geono in sections:
            continue
        sec = Section(
            name=names.get(geono, f"S{geono}"), sec_id=geono, sec_type=BaseTypes.FLATBAR, h=hz, w_top=bt, w_btn=bb
        )
        sections[geono] = sec
    mesh.sections = sections


@dataclasses.dataclass
class _Loader:
    """Reads stored cases of the SIN, one at a time, as role fields in external numbers."""

    path: pathlib.Path
    super_element: Any
    numbering: Numbering
    mesh: Any

    def __call__(self, steps, roles, elements, complex_phase) -> Iterator:
        from ada.fem.formats.sesam.results import read_sin

        wanted = {_FIELD_OF_ROLE[r] for r in roles} if roles is not None else set(RAW_ROLES)
        wanted = {f for f in wanted if f}
        kwargs: dict[str, Any] = {}
        if elements is not None:
            kwargs["forces_elements"] = self.numbering.internal_elements(elements)
        if complex_phase:
            kwargs["complex_phase"] = complex_phase
        sin = _open(self.path, self.super_element)
        with read_sin.SinStreamReader(sin, **kwargs) as stream:
            for step in steps:
                result = stream._load_step(int(step), requested_fields=set(wanted))
                for field in result.results:
                    field.role = RAW_ROLES.get(field.name)
                shown_fields(result.results, self.numbering)
                result.mesh = self.mesh
                yield int(step), result


__all__ = [
    "Numbering",
    "SECTION_KIND_OF_CARD",
    "RAW_ROLES",
    "open_sin_result",
    "numbering_of_mesh",
    "shown_mesh",
    "shown_fields",
    "read_result_points",
    "read_concept_names",
    "read_restrained_nodes",
    "read_shell_pressures",
    "read_load_case_map",
    "pressures_by_result_case",
]
