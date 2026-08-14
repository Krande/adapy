"""BeamCurved carries the analytical curve as its native sweep path."""

import numpy as np

import ada
from ada import BeamCurved
from ada.geom import curves as gc
from ada.geom.solids import FixedReferenceSweptAreaSolid


def _spline():
    # a simple degree-2 open B-spline arc through 3 control points
    return gc.BSplineCurveWithKnots(
        degree=2,
        control_points_list=[(0.0, 0.0, 0.0), (0.5, 1.0, 0.0), (1.0, 0.0, 0.0)],
        curve_form=gc.BSplineCurveFormEnum.UNSPECIFIED,
        closed_curve=False,
        self_intersect=False,
        knots=[0.0, 1.0],
        knot_multiplicities=[3, 3],
        knot_spec=gc.KnotType.UNSPECIFIED,
    )


def test_beam_curved_carries_analytical_curve():
    curve = _spline()
    bm = BeamCurved("bm", (0, 0, 0), (1, 0, 0), curve, "HP180x8")
    # the exact curve object is retained — no sampling / degradation
    assert bm.curve3d is curve
    assert isinstance(bm.curve3d, gc.BSplineCurveWithKnots)


def test_beam_curved_solid_is_a_swept_solid_on_the_exact_directrix():
    curve = _spline()
    bm = BeamCurved("bm", (0, 0, 0), (1, 0, 0), curve, "HP180x8")
    geom = bm.solid_geom()
    assert isinstance(geom.geometry, FixedReferenceSweptAreaSolid)
    # the directrix IS the analytical curve, not a polyline approximation
    assert geom.geometry.directrix is curve


def test_beam_curved_tessellates_upright_along_the_spline(tmp_path):
    # The top-level BSplineCurveWithKnots directrix must actually render: sample the spline into
    # sweep stations and frame the section upright. Exercised through to_gltf, which frames the
    # sweep with the SAME producer-side stations on both render paths — OCC ThruSections in the
    # pyocc env, the NGEOM/libtess2 stream in the adacpp env — so the swept extents agree to
    # tessellation tolerance. (to_trimesh()/solid_occ() is deliberately NOT used: in the adacpp
    # env that routes to the adacpp CAD backend, which has no bspline curve entity — a different
    # code path from the sweep framing this test covers.)
    import trimesh

    curve = _spline()
    bm = BeamCurved("bm", (0, 0, 0), (1, 0, 0), curve, "HP180x8")
    a = ada.Assembly("t") / (ada.Part("p") / bm)
    glb = tmp_path / "beam_curved.glb"
    a.to_gltf(glb)
    sc = trimesh.load(glb)

    v = (
        np.vstack([np.asarray(g.vertices, dtype=float) for g in sc.geometry.values()])
        if sc.geometry
        else np.zeros((0, 3))
    )
    assert len(v) > 0, "swept solid tessellated to an empty mesh"
    mn, mx = v.min(axis=0), v.max(axis=0)

    # The spline arc runs x 0->1 and bulges to y~0.5 (quadratic bezier peak) — the swept body
    # must span that extent, i.e. it follows the directrix rather than collapsing to a point.
    assert mx[0] - mn[0] > 0.9  # x spans the full arc length
    assert mx[1] - mn[1] > 0.4  # y bulge of the arc is present
    # HP180x8 is 180 mm tall. The section is swept UPRIGHT (its height along the third axis),
    # so the vertical extent must be ~0.18 m, not zero — a rolled/flattened profile would be ~0.
    assert 0.15 < (mx[2] - mn[2]) < 0.21, f"section not upright (z-extent {mx[2] - mn[2]})"


def test_beam_curved_is_a_distinct_beam_type():
    curve = _spline()
    bm = BeamCurved("bm", (0, 0, 0), (1, 0, 0), curve, "HP180x8")
    assert isinstance(bm, ada.Beam)
    # exact-type queries separate it from a straight Beam
    assert type(bm) is BeamCurved
