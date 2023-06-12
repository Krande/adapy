from ada import Direction, Point
from ada.geom.placement import Axis2Placement3D


def ifc_direction(ifc_entity) -> Direction:
    return Direction(ifc_entity.DirectionRatios)


def ifc_point(ifc_entity) -> Point:
    return Point(ifc_entity.Coordinates)


def axis3d(ifc_entity) -> Axis2Placement3D:
    return Axis2Placement3D(
        location=ifc_point(ifc_entity.Location),
        axis=ifc_direction(ifc_entity.Axis),
        ref_direction=ifc_direction(ifc_entity.RefDirection),
    )
