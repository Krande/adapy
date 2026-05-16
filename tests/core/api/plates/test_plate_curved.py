"""Tests for :class:`ada.PlateCurved` — covers the API surface that
``Part.add_plate`` and the rendering pipelines exercise.

PlateCurved is mostly driven by adapy readers that surface a non-
planar face (gxml advanced-face import, loft tool corner transitions).
The expectations here mirror the contract those callers depend on:
construction from a B-spline-backed AdvancedFace, the same ``t`` /
``material`` / ``units`` accessors as :class:`ada.Plate`, deriving
boundary nodes from the wrapped face so the plate slots into a Part,
and round-tripping through ``solid_geom`` / ``solid_occ`` /
``extruded_solid_occ`` for downstream rendering.
"""
from __future__ import annotations

import pytest

import ada
from ada.api.loft import loft_profiles
from ada.base.units import Units
from ada.geom.curves import PolyLoop
from ada.geom.points import Point


def _bspline_loft_face():
    """Build a small mixed-cardinality loft and return the first
    B-spline face produced by OCC's ThruSections in ruled mode.

    Mixing a 4-vertex profile with a 12-vertex profile guarantees OCC
    emits B-spline transition faces at each corner — exactly the
    surface kind PlateCurved needs to wrap in the loft pipeline.
    """
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.Geom import Geom_BSplineSurface
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopoDS import topods

    sharp = PolyLoop(polygon=[
        Point(-1.0, -1.0, 0.0),
        Point(1.0, -1.0, 0.0),
        Point(1.0, 1.0, 0.0),
        Point(-1.0, 1.0, 0.0),
    ])
    # Rounded corner pattern (12 pts, same shape as the loft tool's
    # filleted rectangle output) at z=1, smaller so corner-transition
    # faces are well-defined.
    import math
    r = 0.3
    s = math.sqrt(2) / 2
    w = h = 1.0
    rounded_xy = [
        (-w + r, -h),
        (-w + r - r * s, -h + r - r * s),
        (-w, -h + r),
        (-w, h - r),
        (-w + r - r * s, h - r + r * s),
        (-w + r, h),
        (w - r, h),
        (w - r + r * s, h - r + r * s),
        (w, h - r),
        (w, -h + r),
        (w - r + r * s, -h + r - r * s),
        (w - r, -h),
    ]
    rounded = PolyLoop(polygon=[Point(x, y, 1.0) for x, y in rounded_xy])
    shape = loft_profiles([sharp, rounded], ruled=True, is_solid=True)
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = topods.Face(explorer.Current())
        if BRep_Tool.Surface(face).IsKind(Geom_BSplineSurface.get_type_descriptor()):
            return face
        explorer.Next()
    raise RuntimeError("Mixed-cardinality loft did not produce a BSpline face")


@pytest.fixture
def curved_plate():
    """Reusable PlateCurved fixture backed by a real B-spline loft face.

    Constructed via :meth:`PlateCurved.from_occ_face` so the geometry
    is the raw OCC face (the loft tool's primary use case). The
    AdvancedFace-backed construction path is exercised separately in
    :func:`test_advanced_face_constructor_works_for_basic_case`.
    """
    face = _bspline_loft_face()
    return ada.PlateCurved.from_occ_face("curved_pl", face, t=0.01)


def test_construction_exposes_basic_attrs(curved_plate):
    assert curved_plate.name == "curved_pl"
    assert curved_plate.t == pytest.approx(0.01)
    assert curved_plate.material is not None
    assert curved_plate.material.name == "S420"  # default mat


def test_geom_is_none_for_raw_occ_face_construction(curved_plate):
    """``from_occ_face`` bypasses the AdvancedFace round-trip — the
    Geometry accessor returns ``None`` for plates built that way.
    Callers that need a Geometry must use the ``__init__`` constructor."""
    assert curved_plate.geom is None
    assert curved_plate.solid_geom() is None


def test_solid_occ_returns_valid_face(curved_plate):
    """``solid_occ`` is the raw face (no thickness) — the GLB
    tessellator and IFC writer both fall back to this when extrusion
    isn't available or wanted."""
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer

    shape = curved_plate.solid_occ()
    assert shape is not None
    # At least one face present.
    assert TopExp_Explorer(shape, TopAbs_FACE).More()


def test_extruded_solid_occ_produces_volumetric_prism(curved_plate):
    """The streaming-viewer renderer prefers a prism so the plate
    carries thickness; verify the extrusion path yields a solid with
    non-zero volume."""
    from OCC.Core.BRepGProp import brepgprop_VolumeProperties
    from OCC.Core.GProp import GProp_GProps

    solid = curved_plate.extruded_solid_occ()
    props = GProp_GProps()
    brepgprop_VolumeProperties(solid, props)
    assert props.Mass() > 0.0, "Extruded prism should have non-zero volume"


def test_nodes_derived_from_outer_wire(curved_plate):
    """``Part.add_plate`` walks ``plate.nodes`` — for PlateCurved
    these come from the boundary of the wrapped face. Should be a
    non-empty list of distinct points."""
    nodes = curved_plate.nodes
    assert len(nodes) >= 3, f"expected at least 3 boundary nodes, got {len(nodes)}"
    # Cache contract: a second call returns the same object.
    assert curved_plate.nodes is nodes


def test_units_setter_accepts_same_value_rejects_change(curved_plate):
    """``Part.add_plate`` does ``plate.units = self.units`` which is a
    no-op when the units already match — must NOT raise. A real
    cross-unit conversion isn't supported yet; that path raises so
    the caller knows to construct the plate in the right units."""
    curved_plate.units = "m"      # same as default — no-op
    curved_plate.units = Units.M  # enum form also fine
    with pytest.raises(NotImplementedError):
        curved_plate.units = "mm"


def test_bbox_is_computed_lazily(curved_plate):
    """``bbox()`` shouldn't raise; should cache so re-call returns same instance."""
    bbox = curved_plate.bbox()
    assert bbox is not None
    assert curved_plate.bbox() is bbox


def test_add_to_part_via_division(curved_plate):
    """The Part.__truediv__ smart-append path used by the loft tool —
    ``part /= [Plate(...), PlateCurved(...)]`` — must route through
    ``add_plate`` and surface both plate variants in ``part.plates``."""
    flat = ada.Plate.from_3d_points(
        "flat_pl", [(0, 0, -1), (1, 0, -1), (1, 1, -1), (0, 1, -1)], 0.01
    )
    part = ada.Part("mixed") / [flat, curved_plate]
    plate_list = list(part.plates)
    assert len(plate_list) == 2
    plate_types = {type(p).__name__ for p in plate_list}
    assert plate_types == {"Plate", "PlateCurved"}
    # The PlateCurved's parent should now be the Part.
    assert curved_plate.parent is part


def test_add_plate_returns_same_instance(curved_plate):
    """``Part.add_plate`` returns the plate it accepted; downstream
    code (e.g. the loft helper) relies on this to keep the same
    Python object around for later metadata writes."""
    part = ada.Part("attach-test")
    returned = part.add_plate(curved_plate)
    assert returned is curved_plate
    assert curved_plate in list(part.plates)


def test_round_trip_advanced_face_preserves_bspline_surface():
    """OCC face → AdvancedFace → OCC face round-trip.

    Exercises the full structural path that ``occ_face_to_ada_face``
    + ``make_face_from_geom`` advertise. With the proper
    ``FaceBound`` → ``EdgeLoop`` → ``OrientedEdge`` chain emitted by
    ``process_wire``, the round-trip should produce an OCC face whose
    underlying surface is still a BSpline (the surface kind is
    preserved by ``BRepBuilderAPI_MakeFace`` when the supplied wire
    lies on the supplied surface).
    """
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.Geom import Geom_BSplineSurface
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopoDS import topods

    from ada.cadit.step.read.geom.surfaces import occ_face_to_ada_face
    from ada.geom import Geometry
    from ada.occ.geom import geom_to_occ_geom

    face_in = _bspline_loft_face()

    advanced = occ_face_to_ada_face(face_in)
    assert advanced is not None
    # Bounds should be wrapped in FaceBound now — the OCC builder
    # walks ``face_bound.bound.edge_list``, so a list of raw curves
    # (the pre-refactor shape) would AttributeError here.
    from ada.geom import surfaces as geo_su
    from ada.geom import curves as geo_cu
    assert all(isinstance(b, geo_su.FaceBound) for b in advanced.bounds)
    assert all(isinstance(b.bound, geo_cu.EdgeLoop) for b in advanced.bounds)
    assert all(
        all(isinstance(oe, geo_cu.OrientedEdge) for oe in b.bound.edge_list)
        for b in advanced.bounds
    )

    rebuilt = geom_to_occ_geom(Geometry(id="rt", geometry=advanced))
    exp = TopExp_Explorer(rebuilt, TopAbs_FACE)
    assert exp.More(), "Round-trip produced no faces"
    rebuilt_face = topods.Face(exp.Current())
    surf = BRep_Tool.Surface(rebuilt_face)
    assert surf.IsKind(Geom_BSplineSurface.get_type_descriptor()), (
        "Round-trip lost the BSpline surface kind"
    )


def test_round_trip_occ_face_preserves_face_count():
    """End-to-end exercise of the ``from_occ_face`` path:
    OCC face → PlateCurved → ``solid_occ()`` should yield an OCC
    shape containing exactly one face equivalent to the input. The
    raw-OCC construction route is what the loft tool uses for every
    B-spline corner-transition face, so a regression here would
    silently break the floater / jacket renderings.
    """
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.Geom import Geom_BSplineSurface
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopoDS import topods

    face_in = _bspline_loft_face()
    plate = ada.PlateCurved.from_occ_face("rt", face_in, t=0.01)

    shape_out = plate.solid_occ()
    # solid_occ on the raw-OCC path returns the face we stored.
    assert shape_out is face_in

    # extruded_solid_occ should yield a face-bearing prism whose
    # underlying surface still classifies as a BSpline (the prism
    # inherits the swept surface kind from its source face).
    extruded = plate.extruded_solid_occ()
    explorer = TopExp_Explorer(extruded, TopAbs_FACE)
    found_bspline = False
    while explorer.More():
        surf = BRep_Tool.Surface(topods.Face(explorer.Current()))
        if surf.IsKind(Geom_BSplineSurface.get_type_descriptor()):
            found_bspline = True
            break
        explorer.Next()
    assert found_bspline, (
        "extruded prism lost its BSpline surface — round-trip dropped curvature"
    )
