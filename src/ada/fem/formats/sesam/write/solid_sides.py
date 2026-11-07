"""Sesam's side numbering of its solid elements, for a BEUSLO surface load on a solid face.

From the Sesam Input Interface File description (DNV, 89-7012, the element chapters: "When surface loads are
specified for an element side, the surface numbers shown are used for identification of the surface in question.
The local nodenumbering for each side is defined as follows"), each table transcribed as printed, 1-based in the
element's *Sesam* local node numbering -- the order GELMNT1 lists the nodes in (``SESAM_ORDER``), which for the
quadratic elements is not adapy's native one.

BEUSLO on a solid (same document, BEUSLO): ``SIDE`` is "a six figures number for a hexahedron, a five figures
number for a prism and a four figures number for a tetrahedron, consisting of zeroes with 1 for the side with load.
For instance 000100 for a hexahedron means load on side no. 4"; ``NDOF`` is the number of nodes of the side and
``RLOAD(i)`` the pressure at its ``i``-th node, "a positive value means normal pressure directed into the element"
-- the sense of adapy's (Abaqus's) solid face pressure, so no sign flip.

A face is matched by its node set: the Abaqus face the model names (``ada.fem.surfaces.side_node_indices``) is the
Sesam side with the same nodes; ``tests/core/fem/formats/sesam/test_solid_sides.py`` checks every table that way,
which is what catches a mistyped number.
"""

from __future__ import annotations

from ada.fem.shapes.definitions import SolidShapes

#: ``{shape: (side 1 nodes, side 2 nodes, ...)}``, 1-based Sesam local node numbers, in the order RLOAD is given.
SESAM_SOLID_SIDES = {
    # LHEX (21)
    SolidShapes.HEX8: (
        (5, 6, 7, 8),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 4, 8, 7),
        (4, 1, 5, 8),
        (1, 4, 3, 2),
    ),
    # IHEX (20)
    SolidShapes.HEX20: (
        (7, 8, 1, 9, 13, 20, 19, 12),
        (5, 11, 17, 16, 15, 10, 3, 4),
        (1, 2, 3, 10, 15, 14, 13, 9),
        (7, 12, 19, 18, 17, 11, 5, 6),
        (7, 6, 5, 4, 3, 2, 1, 8),
        (19, 20, 13, 14, 15, 16, 17, 18),
    ),
    # TPRI (32); the two triangles printed with a trailing 0
    SolidShapes.WEDGE: (
        (2, 3, 6, 5),
        (3, 1, 4, 6),
        (1, 2, 5, 4),
        (1, 2, 3),
        (6, 5, 4),
    ),
    # IPRI (30)
    SolidShapes.WEDGE15: (
        (3, 4, 5, 9, 14, 13, 12, 8),
        (5, 6, 1, 7, 10, 15, 14, 9),
        (1, 2, 3, 8, 12, 11, 10, 7),
        (1, 2, 3, 4, 5, 6),
        (14, 13, 12, 11, 10, 15),
    ),
    # TETR (33)
    SolidShapes.TETRA: (
        (3, 2, 4),
        (1, 3, 4),
        (2, 1, 4),
        (1, 2, 3),
    ),
    # ITET (31)
    SolidShapes.TETRA10: (
        (3, 4, 5, 9, 10, 8),
        (5, 6, 1, 7, 10, 9),
        (1, 2, 3, 8, 10, 7),
        (1, 6, 5, 4, 3, 2),
    ),
}


def side_code(shape, side: int) -> int:
    """BEUSLO's SIDE for 1-based ``side``: one digit per side, the loaded one 1 (``000100`` = side 4 of six)."""
    n = len(SESAM_SOLID_SIDES[shape])
    return 10 ** (n - side)


def side_of_code(shape, code: int) -> int | None:
    """The 1-based side a BEUSLO ``SIDE`` digit pattern names (:func:`side_code`'s inverse), or None when ``shape``
    has no side table or ``code`` is not one loaded side of it."""
    table = SESAM_SOLID_SIDES.get(shape)
    if table is None:
        return None
    n = len(table)
    return next((side for side in range(1, n + 1) if side_code(shape, side) == code), None)


def abaqus_face(el, side: int) -> int | None:
    """The 0-based Abaqus face of solid ``el`` that is its Sesam ``side`` -- :func:`sesam_side` run backwards, so a
    face read back is the one the writer wrote -- or None when no face is."""
    from ada.fem.shapes.solids import solid_abaqus_faces

    for face in range(len(solid_abaqus_faces.get(el.type, ()))):
        found = sesam_side(el, face)
        if found is not None and found[0] == side:
            return face
    return None


def sesam_side(el, abaqus_side) -> tuple[int, list] | None:
    """``(Sesam side number, its nodes in RLOAD order)`` of the face of solid ``el`` that ``abaqus_side`` names
    (an Abaqus face, ``"S3"`` or the reader's 0-based int), or None when the element type has no side table or the
    side names no single face."""
    from ada.fem.formats.sesam.node_order import SESAM_ORDER
    from ada.fem.surfaces import side_node_indices

    table = SESAM_SOLID_SIDES.get(el.type)
    if table is None:
        return None
    try:
        slots = side_node_indices(el.type, abaqus_side)
    except ValueError:
        return None
    if slots is None:
        return None
    face = {el.nodes[s] for s in slots}
    local = SESAM_ORDER.nodes_to_format(el.type, list(el.nodes))
    for i, side_nodes in enumerate(table, start=1):
        nodes = [local[k - 1] for k in side_nodes]
        if set(nodes) == face:
            return i, nodes
    return None
