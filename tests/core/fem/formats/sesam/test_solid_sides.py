"""Sesam's solid side tables (transcribed from the Input Interface File description) against adapy's Abaqus face
numbering: every face of every solid type is exactly one Sesam side, node set for node set, through the GELMNT1
node order (SESAM_ORDER) for the quadratic ones. A mistyped number in a table fails here."""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Elem
from ada.fem.formats.sesam.write.solid_sides import (
    SESAM_SOLID_SIDES,
    sesam_side,
    side_code,
)
from ada.fem.shapes.definitions import SolidShapes
from ada.fem.shapes.solids import solid_abaqus_faces

CORNERS = {
    SolidShapes.HEX8: [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
    SolidShapes.WEDGE: [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1)],
    SolidShapes.TETRA: [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)],
}
#: native mid-side nodes: the edges in adapy's (Abaqus's) order
EDGES = {
    SolidShapes.HEX20: (
        SolidShapes.HEX8,
        [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)],
    ),
    SolidShapes.WEDGE15: (SolidShapes.WEDGE, [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)]),
    SolidShapes.TETRA10: (SolidShapes.TETRA, [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]),
}


def _element(shape) -> Elem:
    if shape in CORNERS:
        pts = [np.array(c, float) for c in CORNERS[shape]]
    else:
        base, edges = EDGES[shape]
        c = [np.array(p, float) for p in CORNERS[base]]
        pts = c + [(c[a] + c[b]) / 2 for a, b in edges]
    return Elem(1, [ada.Node(tuple(p), i + 1) for i, p in enumerate(pts)], shape)


@pytest.mark.parametrize("shape", sorted(SESAM_SOLID_SIDES, key=str))
def test_every_abaqus_face_is_one_sesam_side_with_the_same_nodes(shape):
    el = _element(shape)
    faces = solid_abaqus_faces[shape]
    assert len(faces) == len(SESAM_SOLID_SIDES[shape])
    seen = set()
    for i in range(len(faces)):
        found = sesam_side(el, i)
        assert found is not None, (shape, f"S{i + 1}")
        number, nodes = found
        assert {n.id for n in nodes} == {el.nodes[s].id for s in faces[i]}
        seen.add(number)
    assert seen == set(range(1, len(faces) + 1)), "each Sesam side is one face"


def test_side_code_is_a_digit_pattern():
    assert side_code(SolidShapes.HEX8, 4) == 100  # 000100
    assert side_code(SolidShapes.HEX8, 1) == 100000
    assert side_code(SolidShapes.WEDGE, 5) == 1
    assert side_code(SolidShapes.TETRA, 4) == 1
