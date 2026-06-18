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

from ada.fem.results.common import Mesh


@dataclass
class AuxRecords:
    """SIN records not surfaced by :class:`Mesh`, keyed by ``geono``/id."""

    thickness_by_geono: dict[int, float] = field(default_factory=dict)

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
            return cls(thickness_by_geono=thickness)
        finally:
            sin.close()


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
