"""Low-level FE extraction helpers for the capacity manager.

These read the few records the high-level :class:`~ada.fem.results.common.Mesh`
does not surface (plate thickness ``GELTH``, eccentricity ``GECCEN``) directly
from the SIN via the low-level ``sin_reader`` — keeping ``ada`` core unchanged —
and provide geometry helpers (element node coordinates, edge spans) used to
derive plate-field dimensions and stiffener spans.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import numpy as np

from ada.fem.capacity.model import CapSection
from ada.fem.results.common import Mesh


@dataclass
class AuxRecords:
    """SIN records not surfaced by :class:`Mesh`, keyed by ``geono``/id.

    Carries the plate thicknesses (``GELTH``) and, as a compatibility fallback,
    the stiffener cross-sections parsed straight from the raw section cards
    (``GIORH`` / ``GBOX`` / ``GLSEC``).
    """

    thickness_by_geono: dict[int, float] = field(default_factory=dict)
    section_by_geono: dict[int, CapSection] = field(default_factory=dict)
    result_point_coords_by_element: dict[int, dict[int, np.ndarray]] = field(default_factory=dict)
    concept_name_by_element: dict[int, str] = field(default_factory=dict)

    @classmethod
    def from_sin(cls, sin_path: str | pathlib.Path) -> "AuxRecords":
        from ada.fem.formats.sesam.results.sin_reader import open_sin

        sin = open_sin(sin_path)
        try:
            thickness: dict[int, float] = {}
            if "GELTH" in sin.type_blocks:
                # GELTH record: (GEONO, TH, ...) — GELREF1.geono of a shell
                # element references this thickness entry.
                for rec in sin.iter_records("GELTH"):
                    if len(rec) >= 2:
                        thickness[int(rec[0])] = float(rec[1])
            names = _section_names(sin)
            sections = _parse_sections(sin, names)
            result_points = _parse_rdpoints(sin)
            return cls(
                thickness_by_geono=thickness,
                section_by_geono=sections,
                result_point_coords_by_element=result_points,
                concept_name_by_element=_parse_concept_names(sin),
            )
        finally:
            sin.close()


# Genie SectionType code for Holland-profile / bulb-flat sections.
_BULB = 7


def _section_names(sin) -> dict[int, str]:
    out: dict[int, str] = {}
    if "TDSECT" not in sin.type_blocks:
        return out
    for prefix, text in sin.iter_text_records("TDSECT"):
        if prefix and text:
            out[int(prefix[0])] = text
    return out


def _parse_sections(sin, names: dict[int, str]) -> dict[int, CapSection]:
    """Stiffener cross-sections by ``geono`` from the raw beam-section cards.

    * ``GIORH`` [geono, H, tw, Bt, Tt, Bb, Tb] — I/T girders (no bottom flange
      when ``Bb == tw``).
    * ``GLSEC`` [geono, H, tw, b, tf] — L-sections, incl. idealized bulb flats
      (``HP*`` names → tagged as bulb so the consumer applies the bulb→angle
      web-height rule).
    * ``GBOX``  [geono, H, ...] — box; carried with zero flange.
    """
    out: dict[int, CapSection] = {}
    for rec in sin.iter_records("GIORH") if "GIORH" in sin.type_blocks else []:
        g = int(rec[0])
        h, tw, bt, tt = float(rec[1]), float(rec[2]), float(rec[3]), float(rec[4])
        out[g] = CapSection(names.get(g, ""), 0, h, tw, bt, tt)
    for rec in sin.iter_records("GLSEC") if "GLSEC" in sin.type_blocks else []:
        g = int(rec[0])
        h, tw, b, tf = float(rec[1]), float(rec[2]), float(rec[3]), float(rec[4])
        is_bulb = names.get(g, "").upper().startswith("HP")
        out[g] = CapSection(names.get(g, ""), _BULB if is_bulb else 0, h, tw, b, tf)
    for rec in sin.iter_records("GBOX") if "GBOX" in sin.type_blocks else []:
        g = int(rec[0])
        out[g] = CapSection(names.get(g, ""), 0, float(rec[1]), float(rec[2]), 0.0, 0.0)
    return out


def _parse_rdpoints(sin) -> dict[int, dict[int, np.ndarray]]:
    """Result-point coordinates by ``element id`` and 1-based point id.

    SIN ``RDPOINTS`` stores absolute coordinates in the variable-width bulk
    payload as ``point id, x, y, z`` groups, with optional ``-1`` separators,
    followed by the element transform matrix. The public result field keeps only
    the point number, so the capacity resolver surfaces the coordinates here.
    """
    if "RDPOINTS" not in sin.type_blocks:
        return {}

    out: dict[int, dict[int, np.ndarray]] = {}
    for rec in sin.iter_records("RDPOINTS"):
        if len(rec) < 9:
            continue
        elno = int(rec[1])
        nsptra = int(rec[6])
        transform_len = nsptra * 9
        bulk = rec[8 : -transform_len] if transform_len else rec[8:]
        points: dict[int, np.ndarray] = {}
        i = 0
        while i < len(bulk):
            point_id = int(bulk[i])
            i += 1
            if point_id == -1:
                continue
            if i + 2 >= len(bulk):
                break
            points[point_id] = np.array((float(bulk[i]), float(bulk[i + 1]), float(bulk[i + 2])), dtype=float)
            i += 3
        if points:
            out[elno] = points
    return out


def _parse_concept_names(sin) -> dict[int, str]:
    """Map FEM element ids to Sesam concept names from TDSCONC/SCONCEPT/SCONMESH."""
    if not all(name in sin.type_blocks for name in ("TDSCONC", "SCONCEPT", "SCONMESH")):
        return {}

    names = {int(prefix[0]): text for prefix, text in sin.iter_text_records("TDSCONC") if prefix and text}
    concept_to_mesh = {
        int(rec[0]): int(rec[-1])
        for rec in sin.iter_records("SCONCEPT")
        if len(rec) >= 7 and int(rec[1]) == 7
    }
    mesh_to_elements = {
        int(rec[0]): tuple(int(e) for e in rec[4:])
        for rec in sin.iter_records("SCONMESH")
        if len(rec) >= 5
    }

    out: dict[int, str] = {}
    for concept_id, mesh_id in concept_to_mesh.items():
        name = names.get(concept_id)
        if not name:
            continue
        for element_id in mesh_to_elements.get(mesh_id, ()):
            out[element_id] = name
    return out


def geono_of(mesh: Mesh, element_id: int) -> int:
    """GELREF1 geometry id (geono) of an element."""
    row = mesh.elem_data[np.where(mesh.elem_data[:, 0] == element_id)[0]]
    if row.size == 0:
        raise KeyError(f"element {element_id} not in mesh")
    return int(row[0][2])


def matno_of(mesh: Mesh, element_id: int) -> int:
    row = mesh.elem_data[np.where(mesh.elem_data[:, 0] == element_id)[0]]
    if row.size == 0:
        raise KeyError(f"element {element_id} not in mesh")
    return int(row[0][1])


def element_node_ids(mesh: Mesh, element_id: int) -> list[int]:
    """Node ids of an element, in connectivity order."""
    for block in mesh.elements:
        hit = np.where(block.identifiers == element_id)[0]
        if hit.size == 0:
            continue
        refs = block.node_refs[hit[0]]
        if block.node_refs_are_indices:
            return [int(n.id) for n in mesh.nodes.get_node_by_index([int(r) for r in refs])]
        return [int(r) for r in refs]
    raise KeyError(f"element {element_id} not in mesh")


def tributary_plate_ids(mesh: Mesh, beam_element_ids: tuple[int, ...], candidate_plate_ids: list[int]) -> list[int]:
    """Plate elements that border a stiffener = those sharing all the beam's nodes.

    A stiffener beam edge is shared by the (up to two) adjacent plate elements;
    those plates carry the stiffener's tributary membrane stresses.
    """
    beam_nodes: set[int] = set()
    for be in beam_element_ids:
        beam_nodes.update(element_node_ids(mesh, be))
    out = []
    for pe in candidate_plate_ids:
        if beam_nodes.issubset(set(element_node_ids(mesh, pe))):
            out.append(pe)
    return out


def element_node_coords(mesh: Mesh, element_id: int) -> np.ndarray:
    """(n_nodes, 3) node coordinates of an element, in connectivity order."""
    for block in mesh.elements:
        hit = np.where(block.identifiers == element_id)[0]
        if hit.size == 0:
            continue
        refs = block.node_refs[hit[0]]
        if block.node_refs_are_indices:
            nodes = mesh.nodes.get_node_by_index([int(r) for r in refs])
        else:
            nodes = mesh.nodes.get_node_by_id([int(r) for r in refs])
        return np.array([n.p for n in nodes], dtype=float)
    raise KeyError(f"element {element_id} not in mesh")


def beam_axis_and_span(mesh: Mesh, element_ids: tuple[int, ...]) -> tuple[np.ndarray, float]:
    """Unit axis vector and total length of a (chain of) beam element(s).

    Span = sum of element lengths; axis = unit vector from the first node to the
    last along the chain (good enough for straight stiffeners).
    """
    coords = [element_node_coords(mesh, e) for e in element_ids]
    span = sum(float(np.linalg.norm(c[-1] - c[0])) for c in coords)
    start = coords[0][0]
    end = coords[-1][-1]
    axis = end - start
    n = np.linalg.norm(axis)
    axis = axis / n if n else np.array([0.0, 0.0, 1.0])
    return axis, span


def plate_dimensions(mesh: Mesh, element_ids: tuple[int, ...], stiffener_axis: np.ndarray) -> tuple[float, float]:
    """(length, width) of a plate field, oriented by the stiffener axis.

    ``length`` is the extent along the stiffener (span direction); ``width`` is
    the extent perpendicular to it (spacing direction). Computed as the bounding
    extent of the field's element nodes projected onto the stiffener axis and an
    in-plane perpendicular.
    """
    pts = np.vstack([element_node_coords(mesh, e) for e in element_ids])
    pts = pts - pts.mean(axis=0)

    axis = np.asarray(stiffener_axis, dtype=float)
    axis = axis / (np.linalg.norm(axis) or 1.0)

    # Plate normal from the first element's first three nodes.
    first = element_node_coords(mesh, element_ids[0])
    normal = np.cross(first[1] - first[0], first[2] - first[0])
    nn = np.linalg.norm(normal)
    normal = normal / nn if nn else np.array([1.0, 0.0, 0.0])

    perp = np.cross(normal, axis)
    pn = np.linalg.norm(perp)
    perp = perp / pn if pn else axis

    length = float(pts @ axis).real if pts.ndim == 1 else float(np.ptp(pts @ axis))
    width = float(np.ptp(pts @ perp))
    return length, width
