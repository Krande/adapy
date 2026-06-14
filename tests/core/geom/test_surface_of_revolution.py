import ifcopenshell

from ada.cadit.ifc.read.geom.surfaces import surface_of_revolution as read_sor
from ada.cadit.ifc.write.geom.surfaces import create_surface_of_revolution as write_sor
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.direction import Direction
from ada.geom.placement import Axis1Placement
from ada.geom.points import Point


def _sor() -> geo_su.SurfaceOfRevolution:
    # A slanted line generatrix revolved about Z -> a cone-like surface of revolution.
    gen = geo_cu.Line(pnt=Point(1, 0, 0), dir=Direction(1, 0, 1))
    return geo_su.SurfaceOfRevolution(
        swept_curve=gen,
        axis_position=Axis1Placement(location=Point(0, 0, 0), axis=Direction(0, 0, 1)),
    )


def test_surface_of_revolution_roundtrip():
    sor = _sor()
    f = ifcopenshell.file(schema="IFC4")
    ifc_sor = write_sor(sor, f)

    assert ifc_sor.is_a("IfcSurfaceOfRevolution")
    # Generatrix is wrapped in an open profile per the IFC SweptCurve type.
    assert ifc_sor.SweptCurve.is_a("IfcArbitraryOpenProfileDef")
    assert ifc_sor.SweptCurve.Curve.is_a("IfcLine")
    assert ifc_sor.AxisPosition.is_a("IfcAxis1Placement")

    back = read_sor(ifc_sor)
    assert isinstance(back, geo_su.SurfaceOfRevolution)
    assert isinstance(back.swept_curve, geo_cu.Line)
    assert back.axis_position.axis.is_equal(Direction(0, 0, 1))
