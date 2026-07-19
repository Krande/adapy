"""Thickened curved-shell export (PlateCurved -> analytic thickness-t ClosedShell).

Covers the kernel-free builder (:func:`ada.geom.primitive_brep.face_to_thick_shell`),
the global thickness-anchor config, the IFC / STEP-stream / NGEOM writer wiring and
the bare-face fallback. The OCC-oracle checks are skipped automatically on the
adacpp backend env (no pythonocc)."""

from __future__ import annotations

import os
import tempfile
from collections import Counter

import pytest

import ada
import ada.geom.curves as cu
import ada.geom.surfaces as su
from ada.config import Config
from ada.geom import Geometry
from ada.geom.curves import KnotType
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.geom.primitive_brep import face_mid_normal, face_to_thick_shell

T = 0.025


def _arch_face() -> su.AdvancedFace:
    """A quadratic-arch B-spline patch (apex z=0.15) with its natural 4-edge bound:
    two B-spline edges (v=0 / v=1 isolines) and two straight edges."""
    surf = su.BSplineSurfaceWithKnots(
        u_degree=2,
        v_degree=1,
        control_points_list=[
            [Point(0, 0, 0), Point(0, 1, 0)],
            [Point(0.5, 0, 0.3), Point(0.5, 1, 0.3)],
            [Point(1, 0, 0), Point(1, 1, 0)],
        ],
        surface_form=su.BSplineSurfaceForm.UNSPECIFIED,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=[3, 3],
        v_multiplicities=[2, 2],
        u_knots=[0.0, 1.0],
        v_knots=[0.0, 1.0],
        knot_spec=KnotType.UNSPECIFIED,
    )

    def spline(y: float) -> cu.BSplineCurveWithKnots:
        return cu.BSplineCurveWithKnots(
            degree=2,
            control_points_list=[Point(0, y, 0), Point(0.5, y, 0.3), Point(1, y, 0)],
            curve_form=cu.BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=False,
            self_intersect=False,
            knot_multiplicities=[3, 3],
            knots=[0.0, 1.0],
            knot_spec=KnotType.UNSPECIFIED,
        )

    p00, p10, p11, p01 = Point(0, 0, 0), Point(1, 0, 0), Point(1, 1, 0), Point(0, 1, 0)
    e0 = cu.EdgeCurve(p00, p10, edge_geometry=spline(0.0), same_sense=True)
    e1 = cu.EdgeCurve(p10, p11, edge_geometry=cu.Line(p10, Direction(0, 1, 0)), same_sense=True)
    e2 = cu.EdgeCurve(p01, p11, edge_geometry=spline(1.0), same_sense=True)  # traversed reversed
    e3 = cu.EdgeCurve(p01, p00, edge_geometry=cu.Line(p01, Direction(0, -1, 0)), same_sense=True)
    loop = cu.EdgeLoop(
        edge_list=[
            cu.OrientedEdge(p00, p10, edge_element=e0, orientation=True),
            cu.OrientedEdge(p10, p11, edge_element=e1, orientation=True),
            cu.OrientedEdge(p11, p01, edge_element=e2, orientation=False),
            cu.OrientedEdge(p01, p00, edge_element=e3, orientation=True),
        ]
    )
    return su.AdvancedFace(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=surf, same_sense=True)


def _patch_z_levels(shell: su.ClosedShell) -> list[list[float]]:
    zs = []
    for f in shell.cfs_faces:
        s = f.face_surface
        if isinstance(s, su.BSplineSurfaceWithKnots):
            zs.append(sorted({round(float(p[2]), 6) for row in s.control_points_list for p in row}))
    return zs


def test_face_mid_normal_probe():
    n = face_mid_normal(_arch_face())
    assert n is not None
    assert abs(n[0]) < 1e-9 and abs(n[1]) < 1e-9 and abs(n[2] - 1.0) < 1e-9


@pytest.mark.parametrize(
    "anchor, lo",
    [("as_is", 0.0), ("flipped", -T), ("centerline", -T / 2)],
)
def test_thickness_anchor_offsets(anchor, lo):
    shell = face_to_thick_shell(_arch_face(), (0, 0, 1), T, anchor=anchor, direction_agrees_with_face=True)
    assert shell is not None and len(shell.cfs_faces) == 6
    zs = _patch_z_levels(shell)
    assert sorted({round(lo, 6), round(lo + 0.3, 6)}) in zs  # bottom patch control net
    assert sorted({round(lo + T, 6), round(lo + T + 0.3, 6)}) in zs  # top patch control net


def test_sense_flag_flips_material_side():
    shell = face_to_thick_shell(_arch_face(), (0, 0, -1), T, anchor="as_is", direction_agrees_with_face=False)
    assert shell is not None
    zs = _patch_z_levels(shell)
    assert sorted({round(-T, 6), round(-T + 0.3, 6)}) in zs  # material grew on the -z side


def test_shell_is_topologically_shared():
    shell = face_to_thick_shell(_arch_face(), (0, 0, 1), T)
    use = Counter()
    for f in shell.cfs_faces:
        for fb in f.bounds:
            for oe in fb.bound.edge_list:
                use[id(oe.edge_element)] += 1
    # every edge (boundary + connector) is used by exactly two faces -> closed 2-manifold
    assert use and all(v == 2 for v in use.values())


def test_unbuildable_input_returns_none():
    poly_bound = su.FaceBound(
        bound=cu.PolyLoop(polygon=[Point(0, 0, 0), Point(1, 0, 0), Point(1, 1, 0)]), orientation=True
    )
    bad = su.AdvancedFace(bounds=[poly_bound], face_surface=_arch_face().face_surface, same_sense=True)
    assert face_to_thick_shell(bad, (0, 0, 1), T) is None
    assert face_to_thick_shell(_arch_face(), (0, 0, 1), 0.0) is None


def _thick_assembly() -> tuple[ada.Assembly, ada.PlateCurved]:
    a = ada.Assembly("A") / (ada.Part("P") / ada.PlateCurved("curved1", Geometry("synthpl", _arch_face(), None), t=T))
    pl = next(o for o in a.get_all_physical_objects() if isinstance(o, ada.PlateCurved))
    return a, pl


def test_plate_curved_solid_geom_is_thick_shell_and_config_off_restores_bare_face():
    _, pl = _thick_assembly()
    assert isinstance(pl.solid_geom().geometry, su.ClosedShell)
    os.environ["ADA_GEOM_THICKEN_CURVED_SHELLS"] = "false"
    try:
        Config().reload_config()
        pl._thick_shell_cache = None
        assert isinstance(pl.solid_geom().geometry, su.AdvancedFace)
    finally:
        os.environ.pop("ADA_GEOM_THICKEN_CURVED_SHELLS")
        Config().reload_config()


def test_ngeom_serializes_thick_shell():
    from ada.cadit.ngeom.serialize import serialize_geometries

    _, pl = _thick_assembly()
    blob = serialize_geometries([("g", pl.solid_geom())])
    assert len(blob) > 100


def test_ifc_export_validates_and_tessellates():
    import ifcopenshell
    import ifcopenshell.geom
    from ifcopenshell.validate import json_logger, validate

    a, _ = _thick_assembly()
    ifc_path = tempfile.mktemp(suffix=".ifc")
    a.to_ifc(ifc_path, validate=False)
    f = ifcopenshell.open(ifc_path)
    assert len(f.by_type("IfcAdvancedBrep")) == 1

    lg = json_logger()
    validate(f, lg, express_rules=True)
    assert lg.statements == []

    it = ifcopenshell.geom.iterator(ifcopenshell.geom.settings(), f)
    assert it.initialize()
    verts = it.get().geometry.verts
    zs = [verts[i] for i in range(2, len(verts), 3)]
    # bezier apex is 0.15; the top face must reach apex + t
    assert max(zs) > 0.15 + T * 0.9


def test_step_stream_roundtrip_volume():
    a, _ = _thick_assembly()
    stp_path = tempfile.mktemp(suffix=".stp")
    a.to_stp(stp_path, writer="stream")
    data = open(stp_path, "rb").read()
    assert b"CLOSED_SHELL" in data

    from ada.cad import active_backend

    be = active_backend()
    if be.name != "pythonocc-core":
        pytest.skip("OCC read-back oracle runs on the pythonocc backend only")
    shape = be.read_step_bytes(data)
    assert be.is_valid(shape)
    vol = be.volume(shape)
    if vol > 1.0:  # reader kept the file's mm units
        vol *= 1e-9
    # rigid-translation sweep volume = t * projected area (= 1.0 m^2 here), exact
    assert abs(vol - T * 1.0) / (T * 1.0) < 0.01


def test_flat_plate_anchor_offsets_extrusion_base():
    pl = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], T)
    base_as_is = pl.solid_geom().geometry.position.location[2]
    os.environ["ADA_GEOM_THICKNESS_ANCHOR"] = "centerline"
    try:
        Config().reload_config()
        base_center = pl.solid_geom().geometry.position.location[2]
        os.environ["ADA_GEOM_THICKNESS_ANCHOR"] = "flipped"
        Config().reload_config()
        base_flipped = pl.solid_geom().geometry.position.location[2]
    finally:
        os.environ.pop("ADA_GEOM_THICKNESS_ANCHOR")
        Config().reload_config()
    assert base_as_is == pytest.approx(0.0)
    assert base_center == pytest.approx(-T / 2)
    assert base_flipped == pytest.approx(-T)


def test_ifc_surface_of_linear_extrusion_write_read_roundtrip():
    """The IfcSurfaceOfLinearExtrusion writer arm (2D profile in the Position frame,
    WR12-compliant) and its reader inverse. Side faces normally prefer the ruled
    B-spline form, so this covers the generic-swept fallback (ellipse / full circle
    off-axis / polyline) directly at the surface level."""
    import ifcopenshell
    import numpy as np

    from ada.cadit.ifc.read.geom.surfaces import (
        surface_of_linear_extrusion as read_sole,
    )
    from ada.cadit.ifc.write.geom.surfaces import create_surface_of_linear_extrusion
    from ada.geom.placement import Axis2Placement3D

    circle = cu.Circle(
        position=Axis2Placement3D(
            location=Point(1.0, 2.0, 3.0), axis=Direction(0.0, 1.0, 0.0), ref_direction=Direction(0.0, 0.0, 1.0)
        ),
        radius=2.5,
    )
    sole = su.SurfaceOfLinearExtrusion(
        swept_curve=circle, position=None, extrusion_direction=Direction(0.0, 0.0, 1.0), depth=0.02
    )

    f = ifcopenshell.file(schema="IFC4")
    ent = create_surface_of_linear_extrusion(sole, f)
    assert ent.is_a("IfcSurfaceOfLinearExtrusion")
    # WR12: the profile curve must be 2D
    assert ent.SweptCurve.Curve.Position.is_a("IfcAxis2Placement2D")

    back = read_sole(ent)
    assert isinstance(back.swept_curve, cu.Circle)
    assert back.swept_curve.radius == pytest.approx(2.5)
    assert np.allclose(list(back.swept_curve.position.location), [1.0, 2.0, 3.0])
    assert np.allclose(list(back.swept_curve.position.axis), [0.0, 1.0, 0.0])
    assert np.allclose(list(back.extrusion_direction), [0.0, 0.0, 1.0])
    assert back.depth == pytest.approx(0.02)


def test_circle_arc_side_face_is_exact_ruled_patch():
    """A circular-arc boundary edge grows a ruled RATIONAL B-spline side face whose
    control net lies exactly on the arc's cylinder (periodic cylinder/SOLE forms are
    untrimmable for the stream kernel)."""
    import math

    from ada.geom.placement import Axis2Placement3D
    from ada.geom.primitive_brep import _circle_arc_bspline

    circle = cu.Circle(
        position=Axis2Placement3D(location=Point(0, 0, 0), axis=Direction(0, 0, 1), ref_direction=Direction(1, 0, 0)),
        radius=2.0,
    )
    a, b = Point(2, 0, 0), Point(0, 2, 0)
    arc = _circle_arc_bspline(circle, a, b, True)
    assert isinstance(arc, cu.RationalBSplineCurveWithKnots)
    # 90 deg arc: single conic segment, w_mid = cos(45 deg)
    assert len(arc.control_points_list) == 3
    assert arc.weights_data[1] == pytest.approx(math.cos(math.pi / 4))
    # endpoints exact, mid control point at r/w on the bisector
    assert list(arc.control_points_list[0]) == pytest.approx([2, 0, 0])
    assert list(arc.control_points_list[2]) == pytest.approx([0, 2, 0])
    assert list(arc.control_points_list[1]) == pytest.approx([2.0, 2.0, 0.0])
    # sampled arc points lie on the circle
    for x, y, z in arc.sample(9):
        assert math.hypot(x, y) == pytest.approx(2.0, abs=1e-9)
        assert z == pytest.approx(0.0, abs=1e-12)
    # full circle (coincident endpoints) is ambiguous -> None
    assert _circle_arc_bspline(circle, a, Point(2, 0, 0), True) is None
