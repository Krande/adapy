"""A planar (faceted-brep / IfcFace) face with an inner bound must cut a hole, not cap it.

Regression for the ``basin-faceted-brep.ifc`` cap: the top rim face carries an
``IfcFaceOuterBound`` plus an ``IfcFaceBound`` (the mouth opening). The shell builder used only
``bounds[0]`` and dropped the inner bound, rendering the opening as a solid cap. ``Face`` faces now
build every bound, subtracting the inner ones as holes.
"""

from __future__ import annotations

import ada.geom.curves as cu
import ada.geom.surfaces as su
from ada.geom.points import Point


def _square(size: float, off: float = 0.0) -> cu.PolyLoop:
    return cu.PolyLoop(
        polygon=[
            Point(off, off, 0.0),
            Point(off + size, off, 0.0),
            Point(off + size, off + size, 0.0),
            Point(off, off + size, 0.0),
        ]
    )


def test_planar_face_inner_bound_is_a_hole():
    from ada.occ.geom.surfaces import _face_area, make_planar_face_from_bounds

    # 10x10 outer with a centred 4x4 hole -> area 100 - 16 = 84.
    bounds = [
        su.FaceBound(bound=_square(10.0), orientation=True),
        su.FaceBound(bound=_square(4.0, off=3.0), orientation=True),
    ]
    face = make_planar_face_from_bounds(bounds)
    assert abs(_face_area(face) - 84.0) < 1e-6, _face_area(face)


def test_planar_face_single_bound_unchanged():
    from ada.occ.geom.surfaces import _face_area, make_planar_face_from_bounds

    face = make_planar_face_from_bounds([su.FaceBound(bound=_square(10.0), orientation=True)])
    assert abs(_face_area(face) - 100.0) < 1e-6, _face_area(face)
