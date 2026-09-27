"""The SAT loop must start at the vertex the plate's outline starts at.

``outline_ccw_about`` rewinds the outline so the loop agrees with the face normal (ACIS
derives the material side from that, see ``test_plate_loop_winds_with_its_normal``). It did
so with a plain ``pts[::-1]``, which is a *start-destroying* reversal: reversing
``[v0, v1, v2, v3]`` yields ``[v3, v2, v1, v0]``, the same cyclic loop entered one step
earlier. Since ``plate_to_sat_entities`` makes ``outline[0]`` the loop's first coedge
(``coedge_ids = [loop.coedge if i == 0 else ...]``), that choice is what the SAT records --
and the reader walks it back faithfully, so the rotation surfaced as a plate whose outline
came back starting at a different corner.

``CurvePoly2d._points_fix`` rewinds the same way but keeps ``points[0]``
(``[points[0]] + reversed(points[1:])``). Matching it costs nothing -- a rotation of a closed
loop is the same loop, same winding, same face -- and it is the only thing that lets the
authored start vertex survive the round trip.

Asserted here on the plain (un-imprinted) plate path, which is where the helper is called
from. The imprinted and curved-plate writers call the same ``outline_ccw_about``
(``writer._add_imprinted_plates``, ``write_curved_plate``), and the imprinted path is covered
end to end by ``tests/core/cadit/gxml/test_gxml_plate_outline_round_trip.py``, which goes
through ``to_genie_xml``'s default.
"""

from __future__ import annotations

import numpy as np
import pytest
from tests.core.cadit.sat.sat_topology import digest, ref_errors

import ada
from ada.cadit.sat.write.write_plate import outline_ccw_about
from ada.cadit.sat.write.writer import part_to_sat_writer

# Authored outlines: a square each way round, a rectangle, and two non-convex shapes whose
# corner order a rotation would scramble far more visibly than a square's.
OUTLINES = {
    "square_ccw": [(0, 0), (1, 0), (1, 1), (0, 1)],
    "square_cw": [(0, 0), (0, 1), (1, 1), (1, 0)],
    "rect_4x3": [(0, 0), (4, 0), (4, 3), (0, 3)],
    "l_shape": [(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)],
    "c_shape": [(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (3, 2), (3, 3), (0, 3)],
}


def _rounded(points) -> list[tuple[float, float, float]]:
    return [tuple(round(float(v), 9) for v in p) for p in points]


def _rotations(points) -> list[list]:
    pts = _rounded(points)
    return [pts[i:] + pts[:i] for i in range(len(pts))]


def _newell(points) -> np.ndarray:
    pts = np.asarray(points, dtype=float)
    n = np.zeros(3)
    for i in range(len(pts)):
        n += np.cross(pts[i], pts[(i + 1) % len(pts)])
    return n


@pytest.mark.parametrize("name", sorted(OUTLINES))
def test_ccw_rewind_keeps_the_first_vertex(name):
    """The rewind may only rotate the loop, and only to the rotation that keeps ``[0]``."""
    pl = ada.Plate("pl", OUTLINES[name], 0.01)
    points3d, normal = pl.outline_global()

    out = _rounded(outline_ccw_about(points3d, normal))
    src = _rounded(points3d)

    assert out in _rotations(src) or out in _rotations(src[::-1]), f"{name}: not the same loop"
    assert out[0] == src[0], f"{name}: start vertex moved {src[0]} -> {out[0]}"
    assert float(np.dot(_newell(out), np.asarray(normal, dtype=float))) > 0, f"{name}: winds against its normal"


@pytest.mark.parametrize("name", sorted(OUTLINES))
def test_the_sat_loop_starts_where_the_outline_starts(name):
    """What the previous test asserts about the helper, read back off the SAT body itself."""
    pl = ada.Plate("pl", OUTLINES[name], 0.01)
    expected = _rounded(pl.outline_global()[0])

    sat = part_to_sat_writer(ada.Assembly("a") / pl, imprint=False).to_str()
    assert ref_errors(sat) == []

    d = digest(sat)
    assert d["faces_walked"] == 1
    boundary = _rounded(d["boundaries"][0])

    assert boundary[0] == expected[0], f"{name}: loop starts at {boundary[0]}, outline at {expected[0]}"
    # The body records exactly what the rewind produced -- no further reordering below it.
    assert boundary == _rounded(outline_ccw_about(*pl.outline_global()))
    # Same polygon (the rewind reverses the winding, hence the reversed rotations), and still
    # wound with the face normal, which is what the rewind exists for.
    assert boundary in _rotations(expected) or boundary in _rotations(expected[::-1])
    assert d["winding_dots"] == [1.0], d["winding_dots"]
