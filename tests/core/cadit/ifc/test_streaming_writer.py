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
    """A B-spline-boundary plate emits an analytic IfcAdvancedBrep body — streamed as a C++-emitted
    SPF fragment under a typed IfcPlate wrapper when adacpp's ``ngeom_to_ifc_body_spf`` is present,
    else via the normal ifcopenshell writer. Either way: valid IFC (schema + EXPRESS where-rules),
    metre units, renders in ifcopenshell's own geometry engine, and round-trips through ada as a
    parametric Plate whose OCC solid passes BRepCheck (ada's OCC harness, same as the STEP path)."""
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


def _arch_plate_curved() -> ada.PlateCurved:
    """A synthetic curved shell: quadratic-arch B-spline patch with its natural 4-edge bound
    (mirrors tests/core/api/plates/test_plate_curved_thick.py)."""
    import ada.geom.curves as cu
    import ada.geom.surfaces as su
    from ada.geom import Geometry
    from ada.geom.curves import KnotType
    from ada.geom.direction import Direction
    from ada.geom.points import Point

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
    e2 = cu.EdgeCurve(p01, p11, edge_geometry=spline(1.0), same_sense=True)
    e3 = cu.EdgeCurve(p01, p00, edge_geometry=cu.Line(p01, Direction(0, -1, 0)), same_sense=True)
    loop = cu.EdgeLoop(
        edge_list=[
            cu.OrientedEdge(p00, p10, edge_element=e0, orientation=True),
            cu.OrientedEdge(p10, p11, edge_element=e1, orientation=True),
            cu.OrientedEdge(p11, p01, edge_element=e2, orientation=False),
            cu.OrientedEdge(p01, p00, edge_element=e3, orientation=True),
        ]
    )
    face = su.AdvancedFace(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=surf, same_sense=True)
    return ada.PlateCurved("curved1", Geometry("synthpl", face, None), t=0.025)


def _typed_mix_model() -> ada.Assembly:
    """Beam + flat plate + spline-boundary plate + thick curved shell — one of each writer path."""
    p = ada.Part("P")
    p.add_beam(ada.Beam("bm1", (0, 0, 2), (1, 0, 2), "IPE200"))
    p.add_plate(ada.Plate("flat1", [(0, 0), (1, 0), (1, 1), (0, 1)], 20e-3))
    p.add_plate(_spline_plate())
    p.add_plate(_arch_plate_curved())
    return ada.Assembly("A") / p


def test_streaming_typed_roundtrip_matches_python_writer(tmp_path):
    """The typed round-trip acceptance bar: the streamed file (C++ B-rep body fragments when adacpp
    is present) re-imports every object as the SAME ada class the normal ifcopenshell writer yields —
    Beam with section+material preserved, parametric Plates for flat and spline-boundary plates, and
    the thick curved shell as whatever the Python writer's file gives (generic shape with full
    geometry). Nothing may come back as a plain proxy-wrapped downgrade relative to the baseline."""
    import ifcopenshell
    from ifcopenshell.validate import json_logger, validate

    streamed, normal = tmp_path / "typed_stream.ifc", tmp_path / "typed_normal.ifc"
    _typed_mix_model().to_ifc(streamed, streaming=True)
    _typed_mix_model().to_ifc(normal)

    fs = ifcopenshell.open(str(streamed))
    assert len(fs.by_type("IfcBeam")) == 1
    assert len(fs.by_type("IfcPlate")) == 3
    assert not fs.by_type("IfcBuildingElementProxy")  # typed products, never proxies

    # zero writer-attributable validation issues (the synthetic model has no rational
    # B-spline surfaces, so even the upstream IfcSurfaceWeightsPositive crash is absent)
    lg = json_logger()
    validate(fs, lg, express_rules=True)
    assert lg.statements == []

    a_s, a_n = ada.from_ifc(streamed), ada.from_ifc(normal)
    for name in ("bm1", "flat1", "spline_pl", "curved1"):
        obj_s, obj_n = a_s.get_by_name(name), a_n.get_by_name(name)
        assert obj_s is not None and obj_n is not None, name
        assert type(obj_s) is type(obj_n), f"{name}: streamed {type(obj_s)} != normal {type(obj_n)}"

    bm = a_s.get_by_name("bm1")
    assert isinstance(bm, ada.Beam)
    assert bm.section.name == "IPE200"
    assert bm.material.name == a_n.get_by_name("bm1").material.name
    assert isinstance(a_s.get_by_name("flat1"), ada.Plate)
    assert isinstance(a_s.get_by_name("spline_pl"), ada.Plate)
