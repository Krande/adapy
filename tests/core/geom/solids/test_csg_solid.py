import ifcopenshell

from ada.cadit.ifc.read.geom.geom_reader import import_geometry_from_ifc_geom
from ada.geom import Geometry
from ada.geom import solids as geo_so


def _block(f, axis, x, y, z):
    return f.create_entity("IfcBlock", axis, x, y, z)


def _axis(f):
    return f.create_entity("IfcAxis2Placement3D", f.create_entity("IfcCartesianPoint", (0.0, 0.0, 0.0)))


def test_csg_solid_wrapping_primitive():
    f = ifcopenshell.file(schema="IFC4")
    csg = f.create_entity("IfcCsgSolid", _block(f, _axis(f), 1.0, 2.0, 3.0))

    geom = import_geometry_from_ifc_geom(csg)
    assert isinstance(geom, geo_so.Box)
    assert (geom.x_length, geom.y_length, geom.z_length) == (1.0, 2.0, 3.0)


def test_csg_solid_wrapping_boolean_result():
    f = ifcopenshell.file(schema="IFC4")
    axis = _axis(f)
    cut = f.create_entity("IfcBooleanResult", "DIFFERENCE", _block(f, axis, 2.0, 2.0, 2.0), _block(f, axis, 1.0, 1.0, 1.0))
    csg = f.create_entity("IfcCsgSolid", cut)

    geom = import_geometry_from_ifc_geom(csg)
    # A boolean tree reads back as a base Geometry carrying the cut as a bool operation.
    assert isinstance(geom, Geometry)
    assert len(geom.bool_operations) == 1
