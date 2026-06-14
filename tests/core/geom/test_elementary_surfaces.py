import ifcopenshell

from ada.cadit.ifc.read.geom.surfaces import (
    cylindrical_surface as read_cyl,
    spherical_surface as read_sph,
    toroidal_surface as read_tor,
)
from ada.cadit.ifc.write.geom.surfaces import (
    create_cylindrical_surface,
    create_spherical_surface,
    create_toroidal_surface,
)
from ada.geom import surfaces as geo_su
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point


def _pos() -> Axis2Placement3D:
    return Axis2Placement3D(Point(1, 2, 3), Direction(0, 0, 1), Direction(1, 0, 0))


def test_cylindrical_surface_roundtrip():
    cs = geo_su.CylindricalSurface(position=_pos(), radius=0.75)
    f = ifcopenshell.file(schema="IFC4")
    ifc = create_cylindrical_surface(cs, f)
    assert ifc.is_a("IfcCylindricalSurface")
    back = read_cyl(ifc)
    assert isinstance(back, geo_su.CylindricalSurface)
    assert back.radius == 0.75
    assert back.position.location.is_equal(Point(1, 2, 3))


def test_spherical_surface_roundtrip():
    ss = geo_su.SphericalSurface(position=_pos(), radius=1.25)
    f = ifcopenshell.file(schema="IFC4")
    back = read_sph(create_spherical_surface(ss, f))
    assert isinstance(back, geo_su.SphericalSurface)
    assert back.radius == 1.25


def test_toroidal_surface_roundtrip():
    ts = geo_su.ToroidalSurface(position=_pos(), major_radius=2.0, minor_radius=0.4)
    f = ifcopenshell.file(schema="IFC4")
    ifc = create_toroidal_surface(ts, f)
    assert ifc.is_a("IfcToroidalSurface")
    back = read_tor(ifc)
    assert isinstance(back, geo_su.ToroidalSurface)
    assert (back.major_radius, back.minor_radius) == (2.0, 0.4)
