import ifcopenshell

from ada.cad import active_backend
from ada.cadit.ifc.read.geom.solids import ifc_rectangular_pyramid
from ada.cadit.ifc.write.geom.solids import (
    rectangular_pyramid as write_rectangular_pyramid,
)
from ada.geom import Geometry
from ada.geom import solids as geo_so
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point


def _pyramid() -> geo_so.RectangularPyramid:
    pos = Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0))
    return geo_so.RectangularPyramid(position=pos, x_length=2.0, y_length=3.0, z_length=4.0)


def test_rectangular_pyramid_occ_build():
    occ_shape = active_backend().build(Geometry("pyr", _pyramid()))
    assert active_backend().shape_type(occ_shape) in ("solid", "shell", "compound")


def test_rectangular_pyramid_ifc_roundtrip():
    rp = _pyramid()
    f = ifcopenshell.file(schema="IFC4")
    ifc_rp = write_rectangular_pyramid(rp, f)

    assert ifc_rp.is_a("IfcRectangularPyramid")
    assert ifc_rp.XLength == 2.0
    assert ifc_rp.YLength == 3.0
    assert ifc_rp.Height == 4.0

    back = ifc_rectangular_pyramid(ifc_rp)
    assert isinstance(back, geo_so.RectangularPyramid)
    assert (back.x_length, back.y_length, back.z_length) == (2.0, 3.0, 4.0)
