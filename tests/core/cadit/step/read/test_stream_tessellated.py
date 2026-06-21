"""STEP AP242 tessellated-geometry coverage: TRIANGULATED_FACE_SET & friends.

These import directly to a native TriangulatedFaceSet (indices kept 1-based to match the
IFC convention). No geometry left behind.
"""

from __future__ import annotations

import ada.geom.surfaces as gs
from ada.cadit.step.read import stream_reader as sr
from ada.geom.points import Point


class _PoolResolver:
    """Resolves a COORDINATES_LIST ref to a list of Points."""

    def __init__(self, coords):
        self._coords = coords

    def deref(self, ref):
        return self._coords


_COORDS = [Point(0, 0, 0), Point(1, 0, 0), Point(1, 1, 0), Point(0, 1, 0)]


def test_coordinates_list_imports():
    pts = sr._b_coordinates_list(_PoolResolver(None), ["", 4, [(0, 0, 0), (1, 0, 0), (1, 1, 0)]])
    assert len(pts) == 3 and isinstance(pts[0], Point)


def test_triangulated_face_set_imports():
    r = _PoolResolver(_COORDS)
    # TRIANGULATED_FACE_SET('', #coords, (normals), closed, (pnindex), (triangles))
    normals = [(0.0, 0.0, 1.0), (0.0, 0.0, 1.0)]
    triangles = [[1, 2, 3], [1, 3, 4]]
    tfs = sr._b_triangulated_face_set(r, ["", sr._Ref(1), normals, sr._Enum("F"), [], triangles])
    assert isinstance(tfs, gs.TriangulatedFaceSet)
    assert len(tfs.coordinates) == 4
    assert tfs.indices == [1, 2, 3, 1, 3, 4]  # flattened, 1-based
    assert len(tfs.normals) == 2


def test_tessellated_shell_single_item_passthrough():
    tfs = gs.TriangulatedFaceSet(coordinates=_COORDS, normals=[], indices=[1, 2, 3])

    class _R:
        def deref(self, x):
            return tfs

    out = sr._b_tessellated_shell(_R(), ["", [sr._Ref(1)]])
    assert out is tfs


def test_tessellated_types_registered():
    for t in ("COORDINATES_LIST", "TRIANGULATED_FACE_SET", "TRIANGULATED_SURFACE_SET",
              "COMPLEX_TRIANGULATED_FACE_SET", "TESSELLATED_SHELL", "TESSELLATED_SOLID"):
        assert t in sr._BUILDERS
