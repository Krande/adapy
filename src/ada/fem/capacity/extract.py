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

    Carries the plate thicknesses (``GELTH``), shell pressures (``BEUSLO``),
    and, as a compatibility fallback, the stiffener cross-sections parsed
    straight from the raw section cards (``GIORH`` / ``GBOX`` / ``GLSEC``).
    """

    thickness_by_geono: dict[int, float] = field(default_factory=dict)
    section_by_geono: dict[int, CapSection] = field(default_factory=dict)
    result_point_coords_by_element: dict[int, dict[int, np.ndarray]] = field(default_factory=dict)
    element_transform_by_element: dict[int, np.ndarray] = field(default_factory=dict)
    concept_name_by_element: dict[int, str] = field(default_factory=dict)
    pressure_by_case_element: dict[int, dict[int, float]] = field(default_factory=dict)

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
            result_points, transforms = _parse_rdpoints(sin)
            return cls(
                thickness_by_geono=thickness,
                section_by_geono=sections,
                result_point_coords_by_element=result_points,
                element_transform_by_element=transforms,
                concept_name_by_element=_parse_concept_names(sin),
                pressure_by_case_element=_parse_shell_pressures(sin),
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


def _parse_rdpoints(sin) -> tuple[dict[int, dict[int, np.ndarray]], dict[int, np.ndarray]]:
    """Result-point coordinates and element transforms by element id.

    SIN ``RDPOINTS`` stores absolute coordinates in the variable-width bulk
    payload as ``point id, x, y, z`` groups, with optional ``-1`` separators,
    followed by the element transform matrix. The public result field keeps only
    the point number, so the capacity resolver surfaces the coordinates and
    local-to-global stress basis here.
    """
    if "RDPOINTS" not in sin.type_blocks:
        return {}, {}

    out: dict[int, dict[int, np.ndarray]] = {}
    transforms: dict[int, np.ndarray] = {}
    for rec in sin.iter_records("RDPOINTS"):
        if len(rec) < 9:
            continue
        elno = int(rec[1])
        nsptra = int(rec[6])
        transform_len = nsptra * 9
        bulk = rec[8:-transform_len] if transform_len else rec[8:]
        if transform_len:
            transform = np.array(rec[-transform_len:], dtype=float).reshape((nsptra, 3, 3))
            if len(transform):
                transforms[elno] = transform[0]
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
    return out, transforms


def _parse_concept_names(sin) -> dict[int, str]:
    """Map FEM element ids to Sesam concept names from TDSCONC/SCONCEPT/SCONMESH."""
    if not all(name in sin.type_blocks for name in ("TDSCONC", "SCONCEPT", "SCONMESH")):
        return {}

    names = {int(prefix[0]): text for prefix, text in sin.iter_text_records("TDSCONC") if prefix and text}
    concept_to_mesh = {
        int(rec[0]): int(rec[-1]) for rec in sin.iter_records("SCONCEPT") if len(rec) >= 7 and int(rec[1]) == 7
    }
    mesh_to_elements = {
        int(rec[0]): tuple(int(e) for e in rec[4:]) for rec in sin.iter_records("SCONMESH") if len(rec) >= 5
    }

    out: dict[int, str] = {}
    for concept_id, mesh_id in concept_to_mesh.items():
        name = names.get(concept_id)
        if not name:
            continue
        for element_id in mesh_to_elements.get(mesh_id, ()):
            out[element_id] = name
    return out


def _parse_shell_pressures(sin) -> dict[int, dict[int, float]]:
    """Mean signed shell pressure from ``BEUSLO`` records by case and element.

    Observed SIN records store ``load case, ..., element id, n values, ...``
    followed by one pressure value per shell node. Values are in SI pressure
    units and retain the Sesam sign convention; the capacity resolver maps them
    to the DNV line-load input without changing that sign.
    """
    if "BEUSLO" not in sin.type_blocks:
        return {}

    out: dict[int, dict[int, float]] = {}
    for rec in sin.iter_records("BEUSLO"):
        if len(rec) < 9:
            continue
        case = int(rec[0])
        element_id = int(rec[4])
        n_values = int(rec[5]) if len(rec) > 5 and rec[5] > 0 else len(rec) - 8
        values = [float(x) for x in rec[8 : 8 + n_values]]
        if not values:
            continue
        out.setdefault(case, {})[element_id] = float(np.mean(values))
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


@dataclass
class _NodeIndex:
    """One-pass connectivity index for a mesh, cached on the mesh.

    Element/node lookups via :func:`element_node_ids` / ``element_node_coords``
    and the tributary search used to scan every block (``np.where`` per call,
    O(n_elements) each), making panel reconstruction O(n^2) — ~23 h on a 128k-
    element topside. This builds the maps once (O(n_elements)) so every lookup
    is O(1) and tributary is O(node degree).
    """

    elem_nodes: dict[int, tuple[int, ...]]
    node_coord: dict[int, np.ndarray]
    node_elems: dict[int, list[int]]
    area: dict[int, float] = field(default_factory=dict)


def element_area(mesh: Mesh, element_id: int) -> float:
    """Planar polygon area of a shell element (cached).

    Frame-invariant, so the rectangularity test can sum cached areas instead of
    re-projecting and shoelacing every plate on every merge attempt.
    """
    idx = _ensure_index(mesh)
    cached = idx.area.get(element_id)
    if cached is not None:
        return cached
    coords = element_node_coords(mesh, element_id)
    if len(coords) < 3:
        a = 0.0
    else:
        normal_sum = np.zeros(3)
        for i in range(1, len(coords) - 1):
            normal_sum = normal_sum + np.cross(coords[i] - coords[0], coords[i + 1] - coords[0])
        a = 0.5 * float(np.linalg.norm(normal_sum))
    idx.area[element_id] = a
    return a


def _ensure_index(mesh: Mesh) -> _NodeIndex:
    idx = getattr(mesh, "_cap_node_index", None)
    if idx is not None:
        return idx
    from collections import defaultdict

    elem_nodes: dict[int, tuple[int, ...]] = {}
    node_coord: dict[int, np.ndarray] = {}
    node_elems: dict[int, list[int]] = defaultdict(list)
    for block in mesh.elements:
        ids = [int(x) for x in block.identifiers]
        refs = block.node_refs
        if block.node_refs_are_indices:
            flat = sorted({int(r) for row in refs for r in row})
            nodes = mesh.nodes.get_node_by_index(flat)
            id_by_ref = {ref: int(n.id) for ref, n in zip(flat, nodes)}
            coord_by_ref = {ref: np.asarray(n.p, dtype=float) for ref, n in zip(flat, nodes)}
            rows = [[id_by_ref[int(r)] for r in row] for row in refs]
            for row_refs, row_ids in zip(refs, rows):
                for ref, nid in zip(row_refs, row_ids):
                    node_coord[nid] = coord_by_ref[int(ref)]
        else:
            flat = sorted({int(r) for row in refs for r in row})
            coord_by_id = {int(n.id): np.asarray(n.p, dtype=float) for n in mesh.nodes.get_node_by_id(flat)}
            rows = [[int(r) for r in row] for row in refs]
            node_coord.update(coord_by_id)
        for element_id, row in zip(ids, rows):
            elem_nodes[element_id] = tuple(row)
            for nid in row:
                node_elems[nid].append(element_id)
    idx = _NodeIndex(elem_nodes=elem_nodes, node_coord=node_coord, node_elems=dict(node_elems))
    try:
        mesh._cap_node_index = idx  # type: ignore[attr-defined]
    except Exception:
        pass
    return idx


def element_node_ids(mesh: Mesh, element_id: int) -> list[int]:
    """Node ids of an element, in connectivity order."""
    idx = _ensure_index(mesh)
    nodes = idx.elem_nodes.get(element_id)
    if nodes is None:
        raise KeyError(f"element {element_id} not in mesh")
    return list(nodes)


def tributary_plate_ids(mesh: Mesh, beam_element_ids: tuple[int, ...], candidate_plate_ids: list[int]) -> list[int]:
    """Plate elements that border a stiffener = those sharing all the beam's nodes.

    A stiffener beam edge is shared by the (up to two) adjacent plate elements;
    those plates carry the stiffener's tributary membrane stresses.
    """
    idx = _ensure_index(mesh)
    beam_nodes: set[int] = set()
    for be in beam_element_ids:
        beam_nodes.update(idx.elem_nodes.get(be, ()))
    if not beam_nodes:
        return []
    # Candidate plates are those sharing at least one of the beam's nodes; a
    # node→element map makes this O(degree) instead of scanning every plate.
    candidates: set[int] = set()
    for nid in beam_nodes:
        candidates.update(idx.node_elems.get(nid, ()))
    allowed = candidates.intersection(candidate_plate_ids)
    return [pe for pe in candidate_plate_ids if pe in allowed and beam_nodes.issubset(idx.elem_nodes.get(pe, ()))]


def _point_segment_dist2(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """Squared distance from point ``p`` to segment ``a``-``b`` (no BLAS)."""
    ab = b - a
    ab2 = float((ab * ab).sum())
    if ab2 <= 1e-20:
        d = p - a
        return float((d * d).sum())
    t = float(((p - a) * ab).sum()) / ab2
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    d = p - (a + t * ab)
    return float((d * d).sum())


def partition_plates_by_nearest_stiffener(
    mesh: Mesh,
    stiffener_lines: list[tuple[str, tuple[int, ...]]],
    plate_ids: list[int],
) -> dict[str, list[int]]:
    """Assign every plate element to its nearest stiffener (full-coverage tributary).

    Unlike :func:`tributary_plate_ids` (only the plates sharing a stiffener's full
    edge), this partitions *all* the panel's plate elements — quads and triangles
    alike — among the stiffeners by centroid distance to each stiffener segment,
    so no element is left uncoloured in the per-stiffener ("individual UF") view.
    Pure indexing/elementwise maths — crash-safe on any BLAS backend.
    """
    segments: list[tuple[str, np.ndarray, np.ndarray]] = []
    for name, eids in stiffener_lines:
        ids = tuple(int(e) for e in eids)
        if not ids:
            continue
        first = element_node_coords(mesh, ids[0])
        last = element_node_coords(mesh, ids[-1])
        segments.append((name, np.asarray(first[0], float), np.asarray(last[-1], float)))

    out: dict[str, list[int]] = {name: [] for name, _ in stiffener_lines}
    if not segments:
        return out
    for pe in plate_ids:
        centroid = element_node_coords(mesh, int(pe)).mean(axis=0)
        best_name = segments[0][0]
        best_d2 = _point_segment_dist2(centroid, segments[0][1], segments[0][2])
        for name, a, b in segments[1:]:
            d2 = _point_segment_dist2(centroid, a, b)
            if d2 < best_d2:
                best_d2 = d2
                best_name = name
        out.setdefault(best_name, []).append(int(pe))
    return out


def element_node_coords(mesh: Mesh, element_id: int) -> np.ndarray:
    """(n_nodes, 3) node coordinates of an element, in connectivity order."""
    idx = _ensure_index(mesh)
    nodes = idx.elem_nodes.get(element_id)
    if nodes is None:
        raise KeyError(f"element {element_id} not in mesh")
    return np.array([idx.node_coord[n] for n in nodes], dtype=float)


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


def stiffener_stations(mesh: Mesh, element_ids: tuple[int, ...]) -> list[list[float]]:
    """The 3 Section-5 stations [start, mid, end] along a stiffener span.

    These are the points where the resolved design stresses/forces are sampled
    (positions 1/2/3): ``start`` = the first node of the chain, ``end`` = the
    last node, ``mid`` = their midpoint. Returns ``[]`` for an empty chain.
    Pure indexing/averaging — no BLAS, so it stays crash-safe on any backend.
    """
    ids = tuple(int(e) for e in element_ids)
    if not ids:
        return []
    coords = [element_node_coords(mesh, e) for e in ids]
    start = np.asarray(coords[0][0], dtype=float)
    end = np.asarray(coords[-1][-1], dtype=float)
    mid = (start + end) / 2.0
    return [start.tolist(), mid.tolist(), end.tolist()]


def stiffener_discretization(mesh: Mesh, element_ids: tuple[int, ...]) -> dict[str, int | bool]:
    """Mesh discretization of a stiffener vs DNV-RP-C201 [6.4.3] (SCM2).

    [6.4.3] requires a minimum of four (4) elements along the stiffener length for
    first-order elements, and a minimum of one (1) for second-order elements.
    Element order is inferred from the node count of the line elements (2 nodes →
    first order, 3 nodes → second order LINE3). Returns
    ``{elements_along, element_order, min_required, ok}``.
    """
    ids = tuple(int(e) for e in element_ids)
    n = len(ids)
    order = 1
    if ids:
        order = 2 if len(element_node_ids(mesh, ids[0])) >= 3 else 1
    min_required = 4 if order == 1 else 1
    return {
        "elements_along": n,
        "element_order": order,
        "min_required": min_required,
        "ok": n >= min_required,
    }


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
