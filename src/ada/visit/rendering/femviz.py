from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import numpy as np

from ada.config import get_logger
from ada.fem.results.common import MeshData
from ada.fem.shapes import ElemShape
from ada.fem.shapes import definitions as shape_def

logger = get_logger()


@dataclass
class ElementRange:
    """Per-element range into the flat triangle buffer.

    ``label`` is the source-file element id (RMED ``MAI/<type>/NUM``,
    SIF/FRD element id, etc.); falls back to a 1-based positional
    counter when the source didn't carry labels.

    ``tri_start`` and ``tri_count`` index the flat triangle buffer
    that ``get_mesh_topology`` returns — i.e. ``faces[3*tri_start :
    3*(tri_start + tri_count)]`` are the indices owned by this
    element. Line elements get ``tri_count == 0``.
    """

    label: int
    tri_start: int
    tri_count: int


@dataclass
class MeshTopology:
    """Bundled edges + faces + per-element triangle ranges.

    Bake passes one of these to the mesh-GLB / edges / elements
    writers so the per-element walk over ``ElemShape`` runs once
    instead of once per writer.
    """

    edges: list = dc_field(default_factory=list)
    faces: list = dc_field(default_factory=list)
    element_ranges: list[ElementRange] = dc_field(default_factory=list)


def get_mesh_topology(mesh: MeshData) -> MeshTopology:
    """Walk the mesh once, emitting edges, the flat triangle list, and
    per-element ``(label, tri_start, tri_count)`` ranges.

    Element labels come from ``CellBlockData.identifiers`` when
    present, falling back to a 1-based positional counter that's
    stable across calls. The triangle-range bookkeeping uses
    ``len(elem_shape.get_faces()) // 3`` per element — same emission
    order as the final flat faces list, so the ranges index it
    directly.
    """

    from ada.fem.shapes.mesh_types import str_to_ada_type

    topo = MeshTopology()
    fallback_idx = 0  # 1-based counter for blocks that lack identifiers
    for cell_block in mesh.cells:
        el_type = str_to_ada_type[cell_block.type]
        block_ids = getattr(cell_block, "identifiers", None)
        for elem_i, elem in enumerate(cell_block.data):
            fallback_idx += 1
            if block_ids is not None:
                label = int(block_ids[elem_i])
            else:
                label = fallback_idx

            elem_shape = ElemShape(el_type, elem)
            topo.edges += elem_shape.edges

            if isinstance(elem_shape.type, shape_def.LineShapes):
                # Line elements contribute edges but no triangles.
                # Record a zero-tri range so the frontend can still
                # report element identity for them later (selection
                # on lines needs an edge-buffer path, not yet wired).
                topo.element_ranges.append(
                    ElementRange(label=label, tri_start=len(topo.faces) // 3, tri_count=0)
                )
                continue

            tri_start = len(topo.faces) // 3
            # ``get_faces()`` (method) splits quad faces of HEX8/HEX20
            # into triangle pairs via hex_face_to_tris before flattening.
            # ``elem_shape.faces`` (property) does *not* — for a HEX mesh
            # it returns 24 indices per cell (6 quads × 4 indices) and
            # reshaping into (-1, 3) produces garbage triangulation
            # crossing quad diagonals incorrectly. Use the method.
            elem_faces = elem_shape.get_faces()
            topo.faces += elem_faces
            tri_count = len(elem_faces) // 3
            topo.element_ranges.append(
                ElementRange(label=label, tri_start=tri_start, tri_count=tri_count)
            )

    return topo


def get_edges_and_faces_from_mesh_data(mesh: MeshData):
    """Backwards-compatible facade over :func:`get_mesh_topology`."""

    topo = get_mesh_topology(mesh)
    return topo.edges, topo.faces


def magnitude(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
