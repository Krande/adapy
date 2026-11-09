"""The face cells of a solid pressure (write_skins): outward, and a flipped quadratic face keeps each mid-side node
between its two corners."""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Elem
from ada.fem.formats.code_aster.write.write_skins import _outward
from ada.fem.shapes.definitions import SolidShapes
from ada.fem.shapes.solids import solid_abaqus_faces


def _hex20() -> Elem:
    corners = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
    pts = [np.array(c, float) for c in corners] + [(np.array(corners[a]) + np.array(corners[b])) / 2 for a, b in edges]
    nodes = [ada.Node(tuple(p), i + 1) for i, p in enumerate(pts)]
    return Elem(1, nodes, SolidShapes.HEX20)


@pytest.mark.parametrize("face", range(6))
def test_every_hex20_face_comes_out_outward_with_its_midside_nodes_in_place(face):
    el = _hex20()
    nodes = _outward([el.nodes[s] for s in solid_abaqus_faces[SolidShapes.HEX20][face]], el)
    c = np.array([n.p for n in nodes[:4]])
    normal = np.cross(c[2] - c[0], c[3] - c[1])
    assert np.dot(normal, c.mean(axis=0) - np.full(3, 0.5)) > 0
    for i, mid in enumerate(nodes[4:]):
        assert np.allclose(mid.p, (c[i] + c[(i + 1) % 4]) / 2), (face, i)


def test_a_face_wound_inward_is_reversed_keeping_its_first_corner():
    el = _hex20()
    # Abaqus's S1 of a C3D20, 1-2-3-4 at z = 0, winds +z: into the element
    inward = [el.nodes[s] for s in solid_abaqus_faces[SolidShapes.HEX20][0]]
    out = _outward(inward, el)
    assert out[0] is inward[0] and [n.id for n in out[:4]] == [1, 4, 3, 2]
    c = np.array([n.p for n in out[:4]])
    for i, mid in enumerate(out[4:]):
        assert np.allclose(mid.p, (c[i] + c[(i + 1) % 4]) / 2)
