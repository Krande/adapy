import ifcopenshell

from ada.cad import active_backend
from ada.cadit.ifc.read.geom.curves import trimmed_curve as read_trimmed_curve
from ada.cadit.ifc.write.geom.curves import create_trimmed_curve as write_trimmed_curve
from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point


def _line_segment() -> geo_cu.TrimmedCurve:
    return geo_cu.TrimmedCurve(
        basis_curve=geo_cu.Line(pnt=Point(0, 0, 0), dir=Direction(0, 0, 1)),
        trim1=Point(0, 0, 0),
        trim2=Point(0, 0, 1),
        master_representation="CARTESIAN",
    )


def _circle_arc() -> geo_cu.TrimmedCurve:
    circle = geo_cu.Circle(
        position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)),
        radius=1.0,
    )
    return geo_cu.TrimmedCurve(
        basis_curve=circle,
        trim1=Point(1, 0, 0),
        trim2=Point(0, 1, 0),
        master_representation="CARTESIAN",
    )


def test_trimmed_line_ifc_roundtrip():
    tc = _line_segment()
    f = ifcopenshell.file(schema="IFC4")
    ifc_tc = write_trimmed_curve(tc, f)

    assert ifc_tc.is_a("IfcTrimmedCurve")
    assert ifc_tc.BasisCurve.is_a("IfcLine")

    read_back = read_trimmed_curve(ifc_tc)
    assert isinstance(read_back, geo_cu.TrimmedCurve)
    assert isinstance(read_back.basis_curve, geo_cu.Line)
    assert read_back.trim1.is_equal(Point(0, 0, 0))
    assert read_back.trim2.is_equal(Point(0, 0, 1))


def test_trimmed_circle_ifc_roundtrip():
    tc = _circle_arc()
    f = ifcopenshell.file(schema="IFC4")
    ifc_tc = write_trimmed_curve(tc, f)

    assert ifc_tc.BasisCurve.is_a("IfcCircle")

    read_back = read_trimmed_curve(ifc_tc)
    assert isinstance(read_back.basis_curve, geo_cu.Circle)
    assert read_back.basis_curve.radius == 1.0
    assert read_back.trim1.is_equal(Point(1, 0, 0))


def test_trimmed_line_occ_build_via_backend():
    # Route the TrimmedCurve->wire build through active_backend().build() by using it as a
    # SweptDiskSolid directrix (a straight segment swept by a disk -> a cylinder).
    sds = geo_so.SweptDiskSolid(directrix=_line_segment(), radius=0.1)
    occ_shape = active_backend().build(Geometry("tc_line", sds))
    assert active_backend().shape_type(occ_shape) in ("solid", "shell", "compound")


def test_trimmed_circle_arc_occ_build_via_backend():
    # A quarter-circle arc directrix swept by a disk -> a quarter torus.
    sds = geo_so.SweptDiskSolid(directrix=_circle_arc(), radius=0.1)
    occ_shape = active_backend().build(Geometry("tc_arc", sds))
    assert active_backend().shape_type(occ_shape) in ("solid", "shell", "compound")
