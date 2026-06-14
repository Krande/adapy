import ifcopenshell

from ada.cad import active_backend
from ada.cadit.ifc.read.geom.curves import composite_curve as read_composite_curve
from ada.cadit.ifc.write.geom.curves import composite_curve as write_composite_curve
from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom.direction import Direction
from ada.geom.points import Point


def _composite() -> geo_cu.CompositeCurve:
    # Two trimmed-line segments forming an L (parent curves must be bounded).
    seg1 = geo_cu.TrimmedCurve(
        basis_curve=geo_cu.Line(pnt=Point(0, 0, 0), dir=Direction(0, 0, 1)),
        trim1=Point(0, 0, 0),
        trim2=Point(0, 0, 1),
        master_representation="CARTESIAN",
    )
    seg2 = geo_cu.TrimmedCurve(
        basis_curve=geo_cu.Line(pnt=Point(0, 0, 1), dir=Direction(0, 1, 0)),
        trim1=Point(0, 0, 1),
        trim2=Point(0, 1, 1),
        master_representation="CARTESIAN",
    )
    return geo_cu.CompositeCurve(
        segments=[
            geo_cu.CompositeCurveSegment(parent_curve=seg1),
            geo_cu.CompositeCurveSegment(parent_curve=seg2),
        ]
    )


def test_composite_curve_ifc_roundtrip():
    cc = _composite()
    f = ifcopenshell.file(schema="IFC4")
    ifc_cc = write_composite_curve(cc, f)

    assert ifc_cc.is_a("IfcCompositeCurve")
    assert len(ifc_cc.Segments) == 2
    assert ifc_cc.Segments[0].ParentCurve.is_a("IfcTrimmedCurve")

    back = read_composite_curve(ifc_cc)
    assert isinstance(back, geo_cu.CompositeCurve)
    assert len(back.segments) == 2
    assert isinstance(back.segments[0].parent_curve, geo_cu.TrimmedCurve)


def test_composite_curve_occ_build_via_backend():
    # Sweep a disk along the composite directrix (routes wire build through the backend).
    sds = geo_so.SweptDiskSolid(directrix=_composite(), radius=0.05)
    occ_shape = active_backend().build(Geometry("cc", sds))
    assert active_backend().shape_type(occ_shape) in ("solid", "shell", "compound")
