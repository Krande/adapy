"""Tests for the memory-bounded streaming IFC writer (to_ifc(streaming=True))."""

import pytest

import ada


def _model():
    p = ada.Part("MyPart")
    p.add_plate(ada.Plate("MyPlate", [(0, 0), (1, 0), (1, 1), (0, 1)], 20e-3))
    p.add_plate(ada.Plate("Tri", [(0, 0), (2, 0), (0, 2)], 10e-3))
    p.add_beam(ada.Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE200"))
    return ada.Assembly("A") / p


def test_streaming_roundtrip(tmp_path):
    dest = tmp_path / "stream.ifc"
    ret = _model().to_ifc(dest, streaming=True)
    assert ret is None  # streaming writes to disk, holds no in-memory file
    assert dest.exists()

    a = ada.from_ifc(dest)
    pl: ada.Plate = a.get_by_name("MyPlate")
    assert isinstance(pl, ada.Plate)
    assert pl.parent.name == "MyPart"
    assert pl.t == 20e-3
    corners = list(dict.fromkeys(tuple(round(c, 6) for c in p) for p in pl.poly.points2d))
    assert len(corners) == 4

    assert isinstance(a.get_by_name("Tri"), ada.Plate)
    assert isinstance(a.get_by_name("MyBeam"), ada.Beam)


def test_streaming_matches_normal_object_count(tmp_path):
    import ifcopenshell

    n = _model().to_ifc(tmp_path / "normal.ifc")
    _model().to_ifc(tmp_path / "stream.ifc", streaming=True)

    fn = ifcopenshell.open(str(tmp_path / "normal.ifc"))
    fs = ifcopenshell.open(str(tmp_path / "stream.ifc"))
    for ifc_class in ("IfcPlate", "IfcBeam"):
        assert len(fs.by_type(ifc_class)) == len(fn.by_type(ifc_class))
    # every streamed plate carries a resolvable representation + placement
    assert all(p.Representation and p.ObjectPlacement for p in fs.by_type("IfcPlate"))
    assert n is not None  # normal path still returns the in-memory file


def test_streaming_fused_from_fem(tmp_path):
    # A part with a FEM shell mesh but no concept plates takes the fused path:
    # Part.iter_objects_from_fem builds + streams one plate per shell element.
    import ifcopenshell
    import ifcopenshell.util.element as ue

    src = ada.Plate("P", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    part = ada.Part("pp")
    part.fem = src.to_fem_obj(0.5, "shell")
    n_shells = len(list(part.fem.elements.shell))
    assert n_shells > 1 and not len(part.plates)  # precondition for the fused path

    (ada.Assembly("A") / part).to_ifc(tmp_path / "fused.ifc", streaming=True)

    g = ifcopenshell.open(str(tmp_path / "fused.ifc"))
    plates = g.by_type("IfcPlate")
    assert len(plates) == n_shells  # 1:1, no coplanar merge in the streaming path
    assert all(p.Representation and p.ObjectPlacement for p in plates)
    assert ue.get_material(plates[0]) is not None
    # colour is a per-plate IfcStyledItem referencing a SHARED IfcSurfaceStyle
    assert len(g.by_type("IfcStyledItem")) >= len(plates)
    assert len(g.by_type("IfcSurfaceStyle")) < len(plates)


def test_streaming_fused_from_fem_analytic(tmp_path):
    # merge_strategy="cylinder" (analytic) emits the FEM shell mesh as ONE recognised-
    # surface B-rep proxy — an IfcShellBasedSurfaceModel of IfcAdvancedFace (tubes become
    # IfcCylindricalSurface, flat panels merged IfcPlane) — not one IfcPlate per element.
    import ifcopenshell

    src = ada.Plate("P", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    part = ada.Part("pp")
    part.fem = src.to_fem_obj(0.5, "shell")
    assert len(list(part.fem.elements.shell)) > 1 and not len(part.plates)

    ret = (ada.Assembly("A") / part).to_ifc(tmp_path / "analytic.ifc", streaming=True, merge_strategy="cylinder")
    assert ret is None and (tmp_path / "analytic.ifc").exists()

    g = ifcopenshell.open(str(tmp_path / "analytic.ifc"))
    assert not g.by_type("IfcPlate")  # analytic path emits no per-element plates
    assert len(g.by_type("IfcShellBasedSurfaceModel")) == 1
    assert len(g.by_type("IfcAdvancedFace")) >= 1  # flat plate → merged analytic face(s)
    assert len(g.by_type("IfcPlane")) >= 1
    prox = g.by_type("IfcBuildingElementProxy")
    assert len(prox) == 1 and prox[0].Representation is not None


def test_streaming_falls_back_for_file_obj_only(tmp_path):
    # streaming needs an on-disk destination; file_obj_only must fall back, not crash
    f = _model().to_ifc(file_obj_only=True, streaming=True)
    assert f is not None
    assert len(f.by_type("IfcPlate")) == 2


def test_streaming_falls_back_for_loaded_ifc(tmp_path):
    # A model loaded from IFC keeps its source entities in ifc_store.f; streaming
    # would rebuild them from scratch and fail, so it must fall back to the
    # in-memory writer (passthrough) instead of crashing or dropping objects.
    import ifcopenshell

    _model().to_ifc(tmp_path / "src.ifc")
    loaded = ada.from_ifc(tmp_path / "src.ifc")
    assert loaded.ifc_store.f.by_type("IfcProduct")  # preloaded → must not stream

    ret = loaded.to_ifc(tmp_path / "out.ifc", streaming=True)
    assert ret is not None  # fell back to the in-memory writer (returns the file)
    g = ifcopenshell.open(str(tmp_path / "out.ifc"))
    assert len(g.by_type("IfcBeam")) == 1 and len(g.by_type("IfcPlate")) == 2


def _spline_plate():
    from ada.api.curves import CurvePoly2d, SplineEdge
    from ada.geom.curves import BSplineCurveFormEnum, BSplineCurveWithKnots, KnotType

    sp = BSplineCurveWithKnots(
        degree=2,
        control_points_list=[(1, 0, 0), (1.3, 0.5, 0), (1, 1, 0)],
        curve_form=BSplineCurveFormEnum.UNSPECIFIED,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[3, 3],
        knots=[0.0, 1.0],
        knot_spec=KnotType.UNSPECIFIED,
    )
    segs = CurvePoly2d.build_edge_segments(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [SplineEdge(a=(1, 0, 0), b=(1, 1, 0), curve=sp)]
    )
    return ada.Plate.from_segments("spline_pl", segs, 0.05)


def test_streaming_spline_plate_is_valid_analytic_ifc(tmp_path):
    """A B-spline-boundary plate routes to the normal writer's analytic IfcAdvancedBrep (the streamed
    SPF text path is for the ~100k plain FEM plates): valid IFC (schema + EXPRESS where-rules), metre
    units, renders in ifcopenshell's own geometry engine, and round-trips through ada as a parametric
    Plate whose OCC solid passes BRepCheck (ada's OCC harness, same as the STEP path)."""
    import ifcopenshell
    import ifcopenshell.geom as ifc_geom
    from ifcopenshell.validate import json_logger, validate

    from ada.cad import active_backend

    dest = tmp_path / "spline_stream.ifc"
    (ada.Assembly("A") / (ada.Part("p") / _spline_plate())).to_ifc(dest, streaming=True)

    f = ifcopenshell.open(str(dest))
    lg = json_logger()
    validate(f, lg, express_rules=True)  # schema + EXPRESS WHERE/global rules
    assert lg.statements == []  # valid IFC
    assert len(f.by_type("IfcAdvancedBrep")) == 1  # analytic B-rep, not a sampled extrusion
    assert len(f.by_type("IfcBSplineSurfaceWithKnots")) == 1  # the exact extruded-spline side face
    assert [u.Name for u in f.by_type("IfcSIUnit") if u.UnitType == "LENGTHUNIT"] == ["METRE"]

    # ifcopenshell's own engine renders it (this is what third-party IFC viewers use)
    n_shapes = 0
    it = ifc_geom.iterator(ifc_geom.settings(), f)
    if it.initialize():
        while True:
            n_shapes += 1
            if not it.next():
                break
    assert n_shapes == 1

    # ada OCC harness: read back -> parametric Plate -> OCC solid -> BRepCheck valid
    plate = ada.from_ifc(dest).get_by_name("spline_pl")
    assert isinstance(plate, ada.Plate)
    assert plate.t == pytest.approx(0.05)
    assert active_backend().is_valid(plate.solid_occ())
