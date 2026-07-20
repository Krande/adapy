"""adapy-written IFC must pass schema + EXPRESS where-rule validation (the offline pillars of the
official buildingSMART validation service — see scripts/validate_ifc.py / the ifc-validate task).

Guards two writer bugs the validator caught:
* a beam used to get TWO IfcRelAssociatesMaterial (the bare per-material rel on top of its
  IfcMaterialProfileSetUsage), violating IfcBuiltElement.MaxOneMaterialAssociation;
* an unused material's eagerly-created rel used to keep an empty RelatedObjects set.
"""

from __future__ import annotations

import ifcopenshell
import pytest
from ifcopenshell.validate import json_logger, validate

import ada


def _model():
    p = ada.Part("p")
    p.add_plate(ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.02))
    p.add_beam(ada.Beam("b", (0, 0, 0), (1, 0, 0), "IPE200"))
    return ada.Assembly("A") / p


@pytest.mark.parametrize("streaming", [False, True], ids=["normal", "streaming"])
def test_written_ifc_passes_express_validation(tmp_path, streaming):
    dest = tmp_path / "m.ifc"
    _model().to_ifc(dest, streaming=streaming)

    f = ifcopenshell.open(str(dest))
    lg = json_logger()
    validate(f, lg, express_rules=True)
    assert lg.statements == [], [s.get("message") for s in lg.statements]

    # exactly ONE material association per element: the beam's is its profile-set usage
    for elem in (*f.by_type("IfcBeam"), *f.by_type("IfcPlate")):
        mat_rels = [r for r in elem.HasAssociations if r.is_a("IfcRelAssociatesMaterial")]
        assert len(mat_rels) == 1, f"{elem.is_a()} '{elem.Name}' has {len(mat_rels)} material associations"
    (beam_rel,) = [r for r in f.by_type("IfcBeam")[0].HasAssociations if r.is_a("IfcRelAssociatesMaterial")]
    assert beam_rel.RelatingMaterial.is_a("IfcMaterialProfileSetUsage")


@pytest.mark.parametrize("streaming", [False, True], ids=["normal", "streaming"])
def test_beam_material_roundtrips_via_profile_set_usage(tmp_path, streaming):
    dest = tmp_path / "m.ifc"
    _model().to_ifc(dest, streaming=streaming)
    b = ada.from_ifc(dest).get_by_name("b")
    assert b.material.name == "S355"
    assert b.section.name == "IPE200"


def _curved_boundary_plate() -> ada.Plate:
    """Square plate with one B-spline edge (bulging +x) and one arc edge (bulging -x)."""
    from ada.api.curves import ArcEdge, CurvePoly2d, SplineEdge
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
    specs = [SplineEdge(a=(1, 0, 0), b=(1, 1, 0), curve=sp), ArcEdge(a=(0, 1, 0), b=(0, 0, 0), midpoint=(-0.1, 0.5, 0))]
    segs = CurvePoly2d.build_edge_segments([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], specs)
    return ada.Plate.from_segments("curved_pl", segs, 0.05)


def test_spline_plate_advanced_brep_is_valid_renders_and_roundtrips(tmp_path):
    """A spline-boundary plate's body is an analytic IfcAdvancedBrep: valid IFC, tessellatable by
    ifcopenshell's OWN engine (what third-party viewers use), and reconstructed as a parametric Plate
    on read-back — line, arc AND spline segments intact."""
    import ifcopenshell
    import ifcopenshell.geom as ifc_geom
    import numpy as np

    from ada.api.curves import ArcSegment, SplineSegment

    dest = tmp_path / "m.ifc"
    (ada.Assembly("A") / (ada.Part("p") / _curved_boundary_plate())).to_ifc(dest)

    f = ifcopenshell.open(str(dest))
    lg = json_logger()
    validate(f, lg, express_rules=True)
    assert lg.statements == [], [s.get("message") for s in lg.statements]
    assert len(f.by_type("IfcAdvancedBrep")) == 1
    assert len(f.by_type("IfcBSplineSurfaceWithKnots")) == 1  # exact extruded-spline side face
    assert len(f.by_type("IfcCylindricalSurface")) == 1  # exact extruded-arc side face

    # renders in ifcopenshell's engine, and the curved edges actually bow out past the unit square
    shp = ifc_geom.create_shape(ifc_geom.settings(), f.by_type("IfcPlate")[0])
    verts = np.array(shp.geometry.verts).reshape(-1, 3)
    assert verts[:, 0].max() > 1.05  # spline bulge
    assert verts[:, 0].min() < -0.05  # arc bulge

    # parametric round-trip: Plate with the analytic segment kinds reconstructed
    pl = ada.from_ifc(dest).get_by_name("curved_pl")
    assert isinstance(pl, ada.Plate)
    assert pl.t == pytest.approx(0.05)
    kinds = {type(s).__name__ for s in pl.poly.segments3d}
    assert SplineSegment.__name__ in kinds and ArcSegment.__name__ in kinds
