"""A plate outline must come back off a Genie XML exactly as it went in.

It used to come back rotated by one vertex -- the same loop, same winding, entered one corner
later. Measured on the unit square: ``poly.points3d`` of ``(0,0,0) (0,1,0) (1,1,0) (1,0,0)``
read back as ``(1,0,0) (0,0,0) (0,1,0) (1,1,0)``.

The chain, measured end to end:

1. ``CurvePoly2d._points_fix`` normalises the authored outline's winding by keeping
   ``points[0]`` and reversing the rest.
2. The SAT writer's ``outline_ccw_about`` rewound it the other way -- ``pts[::-1]``, which
   moves the start vertex -- and ``plate_to_sat_entities`` makes ``outline[0]`` the loop's
   first coedge.
3. The SAT carries that faithfully and ``PlateFactory.get_points`` walks it back faithfully.
4. The read plate's ``CurvePoly2d`` rewinds again about ``points[0]``, which is now the
   rotated start.

So the rotation was authored by the *writer*, in step 2, and the SAT always had room to
carry the right answer. Fixed there rather than canonicalised in the reader, so the outline
start is preserved rather than merely made deterministic.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
import pytest

import ada

# Same shapes as ``tests/core/cadit/sat/test_write_plate_outline_start.py``: a square each way
# round, a rectangle, and two non-convex outlines. The writer authors a single SAT loop per
# plate (``plate_to_sat_entities`` never sets ``Loop.next_loop``), so a plate with a hole
# cannot be expressed on this path and is not covered here.
OUTLINES = {
    "square_ccw": [(0, 0), (1, 0), (1, 1), (0, 1)],
    "square_cw": [(0, 0), (0, 1), (1, 1), (1, 0)],
    "rect_4x3": [(0, 0), (4, 0), (4, 3), (0, 3)],
    "l_shape": [(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)],
    "c_shape": [(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (3, 2), (3, 3), (0, 3)],
}


def _rounded(points) -> list[tuple[float, float, float]]:
    return [tuple(round(float(v), 9) for v in p) for p in points]


def _newell(points) -> np.ndarray:
    pts = np.asarray(points, dtype=float)
    n = np.zeros(3)
    for i in range(len(pts)):
        n += np.cross(pts[i], pts[(i + 1) % len(pts)])
    return n


def _round_trip(outline, tmp_path, name="pl1") -> tuple[ada.Plate, ada.Plate]:
    src = ada.Plate(name, outline, 0.02)
    a = ada.Assembly("rt") / (ada.Part("P") / src)
    xml_file = tmp_path / "plate.xml"
    a.to_genie_xml(xml_file)

    # ``embed_sat`` defaults to "on whenever it can be produced". Without it the plate is
    # written as a bare ``<polygon>``, which preserves vertex order trivially and would make
    # every assertion below pass without exercising the SAT at all.
    faces = ET.parse(xml_file).getroot().findall(".//sheet/sat_reference/face")
    assert faces, "the plate was not written through the SAT; this test would prove nothing"

    read = {pl.name: pl for pl in ada.from_genie_xml(xml_file).get_all_physical_objects(by_type=ada.Plate)}
    assert name in read, f"plate {name!r} is gone after the read ({sorted(read)})"
    return src, read[name]


@pytest.mark.parametrize("name", sorted(OUTLINES))
def test_the_outline_comes_back_vertex_for_vertex(name, tmp_path):
    """Not a cyclic comparison: the same vertices in the same order from the same corner."""
    src, read = _round_trip(OUTLINES[name], tmp_path)
    assert _rounded(read.poly.points3d) == _rounded(src.poly.points3d)


@pytest.mark.parametrize("name", sorted(OUTLINES))
def test_the_outline_keeps_its_winding_and_its_normal(name, tmp_path):
    """A reversal would read as the same polygon to a cyclic comparison while flipping which
    side of the plate is material, so winding and normal are asserted separately from order."""
    src, read = _round_trip(OUTLINES[name], tmp_path)

    assert read.poly.normal == pytest.approx(src.poly.normal)
    a, b = _newell(_rounded(src.poly.points3d)), _newell(_rounded(read.poly.points3d))
    assert b == pytest.approx(a), f"{name}: winding changed ({a} -> {b})"


def test_a_non_convex_outline_keeps_every_corner(tmp_path):
    """The rotation was only ever a rotation, but a C-shape is where a reordering would show:
    its eight corners include two reflex turns that no rotation can produce from the wrong
    order. Pins that the fix did not trade a rotation for a scramble."""
    src, read = _round_trip(OUTLINES["c_shape"], tmp_path, name="c_pl")

    got, want = _rounded(read.poly.points3d), _rounded(src.poly.points3d)
    assert got == want
    assert len(got) == 8
    # Area is order-sensitive for a non-convex loop: a scrambled order self-intersects and
    # loses area. Authored 3x3 minus the 2x1 notch = 7.
    assert abs(_newell(got)[2]) / 2 == pytest.approx(7.0)
