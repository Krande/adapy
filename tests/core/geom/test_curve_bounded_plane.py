import ifcopenshell

from ada.cad import active_backend
from ada.cadit.ifc.read.geom.surfaces import curve_bounded_plane as read_cbp
from ada.cadit.ifc.write.geom.surfaces import curve_bounded_plane as write_cbp
from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point


def _square_curve() -> geo_cu.IndexedPolyCurve:
    return geo_cu.IndexedPolyCurve(
        segments=[
            geo_cu.Edge(Point(0, 0, 0), Point(1, 0, 0)),
            geo_cu.Edge(Point(1, 0, 0), Point(1, 1, 0)),
            geo_cu.Edge(Point(1, 1, 0), Point(0, 1, 0)),
            geo_cu.Edge(Point(0, 1, 0), Point(0, 0, 0)),
        ]
    )


def _cbp() -> geo_su.CurveBoundedPlane:
    plane = geo_su.Plane(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)))
    return geo_su.CurveBoundedPlane(basis_surface=plane, outer_boundary=_square_curve(), inner_boundaries=[])


def test_curve_bounded_plane_occ_build():
    occ_shape = active_backend().build(Geometry("cbp", _cbp()))
    assert active_backend().shape_type(occ_shape) in ("face", "shell", "compound")


def test_curve_bounded_plane_ifc_roundtrip():
    cbp = _cbp()
    f = ifcopenshell.file(schema="IFC4")
    ifc_cbp = write_cbp(cbp, f)

    assert ifc_cbp.is_a("IfcCurveBoundedPlane")
    # OuterBoundary must be an IfcCurve, not a topological IfcEdgeLoop (the previous bug).
    assert ifc_cbp.OuterBoundary.is_a("IfcCurve")
    assert ifc_cbp.OuterBoundary.is_a("IfcIndexedPolyCurve")
    assert ifc_cbp.BasisSurface.is_a("IfcPlane")

    back = read_cbp(ifc_cbp)
    assert isinstance(back, geo_su.CurveBoundedPlane)
    assert isinstance(back.outer_boundary, geo_cu.IndexedPolyCurve)
    assert back.inner_boundaries == []
