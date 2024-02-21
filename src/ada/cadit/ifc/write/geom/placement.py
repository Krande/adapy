from __future__ import annotations

import ifcopenshell

from ada import Point
from ada.cadit.ifc.utils import ifc_p, to_real
from ada.geom.placement import Axis2Placement3D, Direction


def ifc_placement_from_axis3d(axis3d: Axis2Placement3D, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Creates an IfcAxis2Placement3D from an Axis2Placement3D object"""
    location = ifc_p(f, axis3d.location)
    axis = f.create_entity("IfcDirection", to_real(axis3d.axis))
    ref_direction = f.create_entity("IfcDirection", to_real(axis3d.ref_direction))
    return f.create_entity("IfcAxis2Placement3D", location, axis, ref_direction)


def ifc_local_placement():
    ...


def direction(vec: Direction, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Creates an IfcDirection from a Direction object"""
    return f.create_entity("IfcDirection", to_real(vec))


def point(p: Point, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Creates an IfcCartesianPoint from a Point object"""
    return f.create_entity("IfcCartesianPoint", to_real(p))
