"""What strict IFC readers enforce, and drop a body over: a profile's curve is 2D, and a body's
representation type names what its items are (a clipping result is 'Clipping')."""

from __future__ import annotations

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.validate
import pytest

import ada


def _clipped_beam_and_plate() -> ada.Assembly:
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE300")
    bm.add_boolean(ada.BoolHalfSpace(origin=(1.5, 0, 0), normal=(1, 0, 0)))
    pl = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    return ada.Assembly("a") / (ada.Part("p") / [bm, pl])


@pytest.mark.parametrize("schema", ["IFC4", "IFC4X3_add2"])
def test_profiles_are_2d_and_a_clipped_body_is_a_clipping(tmp_path, schema):
    a = _clipped_beam_and_plate()
    a.metadata["schema"] = schema
    dest = tmp_path / "strict.ifc"
    a.to_ifc(dest, streaming=False)
    f = ifcopenshell.open(str(dest))

    for curve in f.by_type("IfcIndexedPolyCurve"):
        if any(inv.is_a("IfcProfileDef") for inv in f.get_inverse(curve)):
            assert curve.Points.is_a("IfcCartesianPointList2D"), curve
    clipped = [r for r in f.by_type("IfcShapeRepresentation") if any(i.is_a("IfcBooleanClippingResult") for i in r.Items)]
    assert clipped and all(r.RepresentationType == "Clipping" for r in clipped)

    log = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, log, express_rules=True)
    assert not log.statements, [s.get("message", "")[:200] for s in log.statements]

    # And the geometry still builds.
    s = ifcopenshell.geom.settings()
    for p in f.by_type("IfcBeam") + f.by_type("IfcPlate"):
        assert ifcopenshell.geom.create_shape(s, p).geometry.verts, p


def _world_bounds(f, name):
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    prod = next(p for p in f.by_type("IfcProduct") if p.Name == name)
    v = ifcopenshell.geom.create_shape(s, prod).geometry.verts
    xs, ys, zs = v[0::3], v[1::3], v[2::3]
    return [round(min(xs), 6), round(min(ys), 6), round(min(zs), 6), round(max(xs), 6), round(max(ys), 6), round(max(zs), 6)]


@pytest.mark.parametrize("schema", ["IFC4", "IFC4X3_add2"])
def test_an_extrusion_is_a_2d_profile_in_its_plane_where_it_was(tmp_path, schema):
    # A flat one away from the origin, and one in a tilted plane (normal +y, x along -z): each must
    # land exactly where the primitive is, from a 2D profile.
    flat = ada.PrimExtrude("flat", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.5, normal=(0, 0, 1), origin=(10, 20, 30), xdir=(1, 0, 0))
    tilted = ada.PrimExtrude("tilted", [(0, 0), (3, 0), (3, 1), (0, 1)], 0.25, normal=(0, 1, 0), origin=(1, 2, 3), xdir=(0, 0, -1))
    a = ada.Assembly("a") / (ada.Part("p") / [flat, tilted])
    a.metadata["schema"] = schema
    dest = tmp_path / "extrude.ifc"
    a.to_ifc(dest, streaming=False)
    f = ifcopenshell.open(str(dest))

    assert all(c.Points.is_a("IfcCartesianPointList2D") for c in f.by_type("IfcIndexedPolyCurve"))
    log = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, log, express_rules=True)
    assert not log.statements, [s.get("message", "")[:200] for s in log.statements]

    assert _world_bounds(f, "flat") == [10, 20, 30, 12, 21, 30.5]
    # Local x along -z, local y = normal x xdir = (0,1,0) x (0,0,-1) = (-1,0,0); extruded along +y.
    assert _world_bounds(f, "tilted") == [0, 2, 0, 1, 2.25, 3]
    # The same box ada's own outline in world coordinates spans, swept along its normal.
    import numpy as np

    for shape in (flat, tilted):
        pts = np.array([list(p) for p in shape.poly.points3d], dtype=float)
        swept = np.vstack([pts, pts + np.array(shape.poly.normal, dtype=float) * shape.extrude_depth])
        expected = [round(v, 6) for v in [*swept.min(axis=0), *swept.max(axis=0)]]
        assert _world_bounds(f, shape.name) == expected, shape.name


@pytest.mark.parametrize("swept", [False, True])
def test_cylinders_and_cones_are_csg_primitives_unless_swept_ones_are_asked_for(tmp_path, swept):
    from ada.cadit.ifc.write.shapes.swept_primitives import SWEPT_PRIMITIVES_KEY

    cyl = ada.PrimCyl("cyl", (1, 2, 3), (1, 2, 5), 0.5)
    cone = ada.PrimCone("cone", (4, 0, 0), (4, 3, 0), 0.25)
    a = ada.Assembly("a") / (ada.Part("p") / [cyl, cone])
    a.metadata["schema"] = "IFC4"
    if swept:
        a.metadata[SWEPT_PRIMITIVES_KEY] = True
    dest = tmp_path / "prims.ifc"
    a.to_ifc(dest, streaming=False)
    f = ifcopenshell.open(str(dest))

    def item(name):
        prod = next(p for p in f.by_type("IfcProduct") if p.Name == name)
        rep = prod.Representation.Representations[0]
        return rep.RepresentationType, rep.Items[0].is_a()

    if not swept:
        assert item("cyl") == ("CSG", "IfcRightCircularCylinder")
        assert item("cone") == ("CSG", "IfcRightCircularCone")
        return
    assert item("cyl") == ("SweptSolid", "IfcExtrudedAreaSolid")
    assert item("cone") == ("SweptSolid", "IfcRevolvedAreaSolid")
    log = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, log, express_rules=True)
    assert not log.statements, [s.get("message", "")[:200] for s in log.statements]
    # The same solids, where the primitives are.
    b = [round(v, 2) for v in _world_bounds(f, "cyl")]
    assert b == [0.5, 1.5, 3.0, 1.5, 2.5, 5.0], b
    b = [round(v, 2) for v in _world_bounds(f, "cone")]
    assert b[1] == 0.0 and b[4] == 3.0 and b[0] == 3.75 and b[3] == 4.25, b


@pytest.mark.parametrize("schema", ["IFC4", "IFC4X3_add2"])
def test_a_revolve_is_a_2d_profile_turned_about_its_plane_y_axis(tmp_path, schema):
    # A 1 x 1 square 1 off the axis, a half turn about the plane's y-axis through (5, 5, 5): it spans
    # 2 either side of the axis in x, from the plane up in z, and 1 along the axis.
    rev = ada.PrimRevolve("rev", [(1, 0), (2, 0), (2, 1), (1, 1)], 180, origin=(5, 5, 5), xdir=(1, 0, 0), normal=(0, 0, 1))
    a = ada.Assembly("a") / (ada.Part("p") / [rev])
    a.metadata["schema"] = schema
    dest = tmp_path / "revolve.ifc"
    a.to_ifc(dest, streaming=False)
    f = ifcopenshell.open(str(dest))

    assert all(c.Points.is_a("IfcCartesianPointList2D") for c in f.by_type("IfcIndexedPolyCurve"))
    log = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, log, express_rules=True)
    assert not log.statements, [s.get("message", "")[:200] for s in log.statements]
    # A half turn, not a whole one: the angle is written in the file's unit, the radian.
    b = [round(v, 2) for v in _world_bounds(f, "rev")]
    assert b in ([3.0, 5.0, 5.0, 7.0, 6.0, 7.0], [3.0, 5.0, 3.0, 7.0, 6.0, 5.0]), b
