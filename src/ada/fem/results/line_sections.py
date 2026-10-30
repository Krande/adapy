"""Line-element section tables: from a design FEM, and grafted onto a solver result's mesh.

Drawing a beam element as a solid needs its cross-section, material and local z axis. A design
:class:`~ada.FEM` has them; most solver results do not. Sesam's SIF carries sections, and adapy's
ODB export writes them to the results SQLite, but a CalculiX ``.frd`` or a Code_Aster MED result is
points and connectivity only. The tables here carry the model's line-element properties across to
the result -- from the solved model, or from the input deck beside the result -- matched by element
id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada.fem import Elem
    from ada.fem.results.common import Mesh


@dataclass
class LineSectionTables:
    """What :class:`~ada.fem.results.common.Mesh` needs to resolve line elements to profiles.

    ``elem_data`` rows are ``(el_id, mat_id, sec_id, vec_id)``. ``end_points`` holds each row's
    first and last node coordinates, ``(n, 2, 3)``, so a graft can check it is matching the same
    element and not only the same number. ``end_nodes``, ``(n, 2)``, the ids of those nodes, when
    known: what a graft needs to add an element the result left out.
    """

    sections: dict
    materials: dict
    vectors: dict
    elem_data: np.ndarray
    end_points: np.ndarray
    end_nodes: np.ndarray | None = None


def line_section_tables(line_elems: Iterable["Elem"]) -> LineSectionTables | None:
    """The section / material / local-z tables of ``line_elems``; None when none has both a section
    and a material. Section and material ids are unset on in-memory objects, so stable per-table ids
    are assigned here."""
    sec_map: dict[int, tuple[int, object]] = {}
    mat_map: dict[int, tuple[int, object]] = {}
    vec_map: dict[tuple | None, int] = {}
    rows = []
    ends = []
    end_ids = []
    for el in line_elems:
        fs = el.fem_sec
        if fs is None or fs.section is None or fs.material is None:
            continue
        sid = sec_map.setdefault(id(fs.section), (len(sec_map) + 1, fs.section))[0]
        mid = mat_map.setdefault(id(fs.material), (len(mat_map) + 1, fs.material))[0]
        lz = fs.local_z
        key = tuple(round(float(x), 9) for x in lz) if lz is not None else None
        vid = vec_map.setdefault(key, len(vec_map))
        rows.append((int(el.id), mid, sid, vid))
        ends.append([np.asarray(el.nodes[0].p, dtype=float), np.asarray(el.nodes[-1].p, dtype=float)])
        end_ids.append([int(el.nodes[0].id), int(el.nodes[-1].id)])
    if not rows:
        return None
    return LineSectionTables(
        sections={sid: sec for sid, sec in sec_map.values()},
        materials={mid: mat for mid, mat in mat_map.values()},
        vectors={vid: (list(k) if k is not None else None) for k, vid in vec_map.items()},
        elem_data=np.array(rows, dtype=np.int64),
        end_points=np.array(ends, dtype=float),
        end_nodes=np.array(end_ids, dtype=np.int64),
    )


def graft_line_sections(
    mesh: "Mesh", tables: LineSectionTables | None, tol: float = 1e-6, add_missing: bool = False
) -> int:
    """Give ``mesh``'s line elements the model's sections; returns how many got one.

    A row is taken only when the result has a line element of that id whose end nodes sit where the
    model's element's do. A solver that renumbers elements then gets no sections rather than wrong
    ones. A mesh that already has sections (Sesam's, an ODB export's) is left as it is.

    ``add_missing``: a row whose element the result does not have at all, but whose end nodes it
    does (same ids, same positions), is added to the mesh as a two-node line element. CalculiX
    writes no element of a user element type (its ``U1`` beam) to the ``.frd``, so a beam model's
    result is its nodes only; the deck's elements are what was solved. An element id the result
    uses for something else is never added.
    """
    from ada.fem.shapes.definitions import LineShapes

    if tables is None or getattr(mesh, "sections", None):
        return 0

    coords = np.asarray(mesh.nodes.coords, dtype=float)
    row_of_id = {int(n): i for i, n in enumerate(np.asarray(mesh.nodes.identifiers))}
    result_ends: dict[int, np.ndarray] = {}
    result_ids: set[int] = set()
    for block in mesh.elements:
        result_ids.update(int(i) for i in block.identifiers)
        if not isinstance(block.elem_info.type, LineShapes):
            continue
        for el_id, refs in zip(block.identifiers, block.node_refs):
            first, last = int(refs[0]), int(refs[-1])
            if not block.node_refs_are_indices:
                if first not in row_of_id or last not in row_of_id:
                    continue
                first, last = row_of_id[first], row_of_id[last]
            result_ends[int(el_id)] = coords[[first, last]]

    keep = []
    added: list[tuple[int, int, int]] = []
    for i, (row, ends) in enumerate(zip(tables.elem_data, tables.end_points)):
        el_id = int(row[0])
        found = result_ends.get(el_id)
        if found is not None:
            if np.allclose(found, ends, atol=tol):
                keep.append(row)
            continue
        if not add_missing or tables.end_nodes is None or el_id in result_ids:
            continue
        n0, n1 = (int(n) for n in tables.end_nodes[i])
        if n0 not in row_of_id or n1 not in row_of_id:
            continue
        if np.allclose(coords[[row_of_id[n0], row_of_id[n1]]], ends, atol=tol):
            keep.append(row)
            added.append((el_id, n0, n1))
    if len(keep) < len(tables.elem_data):
        logger.info(
            "line sections: %d of %d model line elements matched the result by id and position",
            len(keep),
            len(tables.elem_data),
        )
    if not keep:
        return 0

    if added:
        _add_line_block(mesh, added)
    mesh.sections = tables.sections
    mesh.materials = tables.materials
    mesh.vectors = tables.vectors
    mesh.elem_data = np.array(keep, dtype=np.int64)
    return len(keep)


def _add_line_block(mesh: "Mesh", added: list[tuple[int, int, int]]) -> None:
    """Append two-node line elements ``(el_id, node id, node id)`` to ``mesh``, in the node-reference
    convention of its existing blocks (ids unless every block uses row indices)."""
    from ada.fem.results.common import ElementBlock, ElementInfo
    from ada.fem.shapes.definitions import LineShapes

    ids = np.array([a[0] for a in added], dtype=np.int64)
    refs = np.array([[a[1], a[2]] for a in added], dtype=np.int64)
    as_indices = bool(mesh.elements) and all(b.node_refs_are_indices for b in mesh.elements)
    if as_indices:
        row_of_id = {int(n): i for i, n in enumerate(np.asarray(mesh.nodes.identifiers))}
        refs = np.vectorize(row_of_id.__getitem__, otypes=[np.int64])(refs)
    software = mesh.elements[0].elem_info.source_software if mesh.elements else None
    info = ElementInfo(type=LineShapes.LINE, source_software=software, source_type="line")
    mesh.elements = list(mesh.elements) + [
        ElementBlock(elem_info=info, node_refs=refs, identifiers=ids, node_refs_are_indices=as_indices)
    ]
    logger.info("line sections: added %d line element(s) the result file does not carry", len(added))
