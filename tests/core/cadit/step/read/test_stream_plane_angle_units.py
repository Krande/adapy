"""STEP plane-angle unit handling in the kernel-free streaming reader.

STEP tags every plane angle (notably ``CONICAL_SURFACE.semi_angle`` and conic
``TRIMMED_CURVE`` parameter trims) in the unit its ``GLOBAL_UNIT_ASSIGNED_CONTEXT``
declares — commonly degrees. ``ada.geom`` and both kernels want radians, so the reader
must convert. A missed degree->radian conversion turns a shallow cone (semi_angle 1.5 deg)
into a near-degenerate 86 deg flat cone that libtess2 meshes to zero triangles, silently
dropping the face. These tests pin the unit detection and the builder scaling.
"""

from __future__ import annotations

import math

import ada.geom.curves as gc
import ada.geom.surfaces as gs
from ada.cadit.step.read import stream_reader as sr

_DEG = math.pi / 180.0


class _Resolver:
    """Minimal resolver stub carrying an angle scale, like the real ``_Resolver``."""

    def __init__(self, angle_scale: float = 1.0):
        self.angle_scale = angle_scale

    def deref(self, x):
        return x


def _degree_unit_pool() -> dict[int, sr._Rec]:
    """A pool mirroring a real ISO-10303 degree plane-angle unit assignment:
    a GEOMETRIC_REPRESENTATION_CONTEXT whose GLOBAL_UNIT_ASSIGNED_CONTEXT lists a
    length unit, a CONVERSION_BASED_UNIT('DEGREE') plane-angle unit, and a solid-angle unit."""
    return {
        10: sr._Rec(
            sr._COMPLEX,
            {
                "GEOMETRIC_REPRESENTATION_CONTEXT": [3],
                "GLOBAL_UNIT_ASSIGNED_CONTEXT": [[sr._Ref(20), sr._Ref(30), sr._Ref(40)]],
                "REPRESENTATION_CONTEXT": ["id", "kind"],
            },
        ),
        20: sr._Rec(
            sr._COMPLEX,
            {"LENGTH_UNIT": [], "NAMED_UNIT": [sr._STAR], "SI_UNIT": [sr._Enum("MILLI"), sr._Enum("METRE")]},
        ),
        30: sr._Rec(
            sr._COMPLEX,
            {"CONVERSION_BASED_UNIT": ["DEGREE", sr._Ref(31)], "NAMED_UNIT": [sr._Ref(32)], "PLANE_ANGLE_UNIT": []},
        ),
        31: sr._Rec("PLANE_ANGLE_MEASURE_WITH_UNIT", [0.0174532925, sr._Ref(33)]),
        40: sr._Rec(sr._COMPLEX, {"SI_UNIT": [sr._STAR, sr._Enum("STERADIAN")], "SOLID_ANGLE_UNIT": []}),
    }


def _radian_unit_pool() -> dict[int, sr._Rec]:
    return {
        10: sr._Rec(
            sr._COMPLEX,
            {
                "GEOMETRIC_REPRESENTATION_CONTEXT": [3],
                "GLOBAL_UNIT_ASSIGNED_CONTEXT": [[sr._Ref(20), sr._Ref(30)]],
                "REPRESENTATION_CONTEXT": ["id", "kind"],
            },
        ),
        20: sr._Rec(
            sr._COMPLEX,
            {"LENGTH_UNIT": [], "NAMED_UNIT": [sr._STAR], "SI_UNIT": [sr._Enum("MILLI"), sr._Enum("METRE")]},
        ),
        30: sr._Rec(
            sr._COMPLEX, {"PLANE_ANGLE_UNIT": [], "NAMED_UNIT": [sr._STAR], "SI_UNIT": [sr._STAR, sr._Enum("RADIAN")]}
        ),
    }


# --- unit detection ------------------------------------------------------------------------


def test_detect_degree_scale_from_context_scan():
    pool = _degree_unit_pool()
    scale = sr._detect_plane_angle_scale(pool.get, all_recs=pool.values())
    assert scale == 0.0174532925


def test_detect_degree_scale_from_representation():
    pool = _degree_unit_pool()
    pool[100] = sr._Rec("ADVANCED_BREP_SHAPE_REPRESENTATION", ["name", [sr._Ref(200)], sr._Ref(10)])
    scale = sr._detect_plane_angle_scale(pool.get, rep_ids=[100])
    assert scale == 0.0174532925


def test_detect_radian_scale_is_one():
    pool = _radian_unit_pool()
    assert sr._detect_plane_angle_scale(pool.get, all_recs=pool.values()) == 1.0


def test_detect_defaults_to_radian_when_absent():
    assert sr._detect_plane_angle_scale({}.get, all_recs=[]) == 1.0


# --- builder scaling -----------------------------------------------------------------------


def test_conical_surface_semi_angle_scaled_from_degrees():
    surf = sr._b_conical_surface(_Resolver(_DEG), ["", "POS", 5.0, 45.0])
    assert isinstance(surf, gs.ConicalSurface)
    assert math.isclose(surf.semi_angle, math.pi / 4.0, rel_tol=1e-9)


def test_conical_surface_semi_angle_radian_unchanged():
    surf = sr._b_conical_surface(_Resolver(1.0), ["", "POS", 5.0, 1.2])
    assert math.isclose(surf.semi_angle, 1.2, rel_tol=1e-12)


def test_trimmed_conic_parameter_trims_scaled():
    basis = gc.Circle(gs.Axis2Placement3D(location=(0, 0, 0)), radius=2.0)
    tc = sr._b_trimmed_curve(_Resolver(_DEG), ["", basis, [0.0], [90.0], sr._Enum("T"), sr._Enum("PARAMETER")])
    assert math.isclose(tc.trim2, math.pi / 2.0, rel_tol=1e-9)


def test_trimmed_line_parameter_trims_not_scaled():
    # A LINE trim is a length parameter, not an angle — the angle unit must not touch it.
    basis = gc.Line((0, 0, 0), (1, 0, 0))
    tc = sr._b_trimmed_curve(_Resolver(_DEG), ["", basis, [0.0], [5.0], sr._Enum("T"), sr._Enum("PARAMETER")])
    assert tc.trim2 == 5.0


# --- detection -> resolver -> builder wiring (the reader path) ------------------------------


def test_resolver_applies_detected_degree_scale_to_cone():
    """Guards the reader wiring: the detected plane-angle scale reaches ``_Resolver`` and is
    applied when a CONICAL_SURFACE is resolved — the same path ``_read_two_pass_dict`` takes."""
    pool = _degree_unit_pool()
    pool[50] = sr._Rec("AXIS2_PLACEMENT_3D", ["", sr._Ref(51), sr._Ref(52), sr._Ref(53)])
    pool[51] = sr._Rec("CARTESIAN_POINT", ["", [0.0, 0.0, 0.0]])
    pool[52] = sr._Rec("DIRECTION", ["", [0.0, 0.0, 1.0]])
    pool[53] = sr._Rec("DIRECTION", ["", [1.0, 0.0, 0.0]])
    pool[60] = sr._Rec("CONICAL_SURFACE", ["", sr._Ref(50), 4.85, 30.0])  # 30 degrees

    angle_scale = sr._detect_plane_angle_scale(pool.get, all_recs=pool.values())
    resolver = sr._Resolver(pool, angle_scale=angle_scale)
    surf = resolver.resolve(60)
    assert isinstance(surf, gs.ConicalSurface)
    assert math.isclose(surf.semi_angle, math.radians(30.0), rel_tol=1e-7)
