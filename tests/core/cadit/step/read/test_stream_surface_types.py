"""STEP surface-type coverage: swept / bounded / trimmed / offset surfaces.

Each imports into its native ada.geom surface type (no geometry left behind). Tests pin
the reader builders.
"""

from __future__ import annotations

import ada.geom.surfaces as gs
from ada.cadit.step.read import stream_reader as sr
from ada.geom.curves import Line
from ada.geom.direction import Direction


class _IdResolver:
    _pool = {}

    def deref(self, x):
        return x


_T = sr._Enum("T")


def test_axis1_placement_default_axis():
    ax = sr._b_axis1_placement(_IdResolver(), ["", (0.0, 0.0, 0.0)])
    assert list(ax.axis) == [0.0, 0.0, 1.0]


def test_surface_of_revolution_imports():
    s = sr._b_surface_of_revolution(_IdResolver(), ["", Line((0, 0, 0), (0, 0, 1)), "AXIS1"])
    assert isinstance(s, gs.SurfaceOfRevolution) and s.axis_position == "AXIS1"


def test_surface_of_linear_extrusion_imports():
    s = sr._b_surface_of_linear_extrusion(_IdResolver(), ["", Line((0, 0, 0), (1, 0, 0)), Direction(0, 0, 1)])
    assert isinstance(s, gs.SurfaceOfLinearExtrusion)
    assert list(s.extrusion_direction) == [0.0, 0.0, 1.0] and s.depth == 1.0


def test_rectangular_trimmed_surface_imports():
    s = sr._b_rectangular_trimmed_surface(_IdResolver(), ["", gs.Plane(None), 0.0, 1.0, 0.0, 2.0, _T, _T])
    assert isinstance(s, gs.RectangularTrimmedSurface)
    assert (s.u1, s.u2, s.v1, s.v2) == (0.0, 1.0, 0.0, 2.0)


def test_curve_bounded_plane_imports():
    plane = gs.Plane(None)
    s = sr._b_curve_bounded_surface(_IdResolver(), ["", plane, [Line((0, 0, 0), (1, 0, 0))], _T])
    assert isinstance(s, gs.CurveBoundedPlane) and s.basis_surface is plane


def test_offset_surface_imports():
    s = sr._b_offset_surface(_IdResolver(), ["", gs.Plane(None), 2.5, sr._Enum("F")])
    assert isinstance(s, gs.OffsetSurface) and s.distance == 2.5


def test_surface_types_registered():
    for t in (
        "AXIS1_PLACEMENT",
        "SURFACE_OF_REVOLUTION",
        "SURFACE_OF_LINEAR_EXTRUSION",
        "RECTANGULAR_TRIMMED_SURFACE",
        "CURVE_BOUNDED_SURFACE",
        "OFFSET_SURFACE",
    ):
        assert t in sr._BUILDERS
