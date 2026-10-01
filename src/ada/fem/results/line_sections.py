"""Line-element section tables: from a design FEM, and grafted onto a solver result's mesh.

Drawing a beam element as a solid needs its cross-section, material and local z axis. A design
:class:`~ada.FEM` has them; most solver results do not. Sesam's SIF carries sections, but an Abaqus
ODB dump or a Code_Aster MED result is points and connectivity only, so their bundles had no beam
solids. The tables here carry the design model's line-element properties across to the result,
matched by element id.
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
    element and not only the same number.
    """

    sections: dict
    materials: dict
    vectors: dict
    elem_data: np.ndarray
    end_points: np.ndarray


def line_section_tables(line_elems: Iterable["Elem"]) -> LineSectionTables | None:
    """The section / material / local-z tables of ``line_elems``; None when none has both a section
    and a material. Section and material ids are unset on in-memory objects, so stable per-table ids
    are assigned here."""
    sec_map: dict[int, tuple[int, object]] = {}
    mat_map: dict[int, tuple[int, object]] = {}
    vec_map: dict[tuple | None, int] = {}
    rows = []
    ends = []
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
    if not rows:
        return None
    return LineSectionTables(
        sections={sid: sec for sid, sec in sec_map.values()},
        materials={mid: mat for mid, mat in mat_map.values()},
        vectors={vid: (list(k) if k is not None else None) for k, vid in vec_map.items()},
        elem_data=np.array(rows, dtype=np.int64),
        end_points=np.array(ends, dtype=float),
    )


def graft_line_sections(mesh: "Mesh", tables: LineSectionTables | None, tol: float = 1e-6) -> int:
    """Give ``mesh``'s line elements the design model's sections; returns how many got one.

    A row is taken only when the result has a line element of that id whose end nodes sit where the
    design element's do. A solver that renumbers elements then gets no sections rather than wrong
    ones. A mesh that already has sections (Sesam's) is left as it is.
    """
    from ada.fem.shapes.definitions import LineShapes

    if tables is None or getattr(mesh, "sections", None):
        return 0

    coords = np.asarray(mesh.nodes.coords, dtype=float)
    row_of_id = {int(n): i for i, n in enumerate(np.asarray(mesh.nodes.identifiers))}
    result_ends: dict[int, np.ndarray] = {}
    for block in mesh.elements:
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
    for row, ends in zip(tables.elem_data, tables.end_points):
        found = result_ends.get(int(row[0]))
        if found is not None and np.allclose(found, ends, atol=tol):
            keep.append(row)
    if len(keep) < len(tables.elem_data):
        logger.info(
            "line sections: %d of %d design line elements matched the result by id and position",
            len(keep),
            len(tables.elem_data),
        )
    if not keep:
        return 0

    mesh.sections = tables.sections
    mesh.materials = tables.materials
    mesh.vectors = tables.vectors
    mesh.elem_data = np.array(keep, dtype=np.int64)
    return len(keep)
