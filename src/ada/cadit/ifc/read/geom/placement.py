from ada import Direction, Point
from ada.geom.placement import Axis1Placement, Axis2Placement3D


def ifc_direction(ifc_entity) -> Direction:
    return Direction(ifc_entity.DirectionRatios)


def ifc_point(ifc_entity) -> Point:
    return Point(ifc_entity.Coordinates)


def axis3d(ifc_entity) -> Axis2Placement3D:
    ref_dir = ifc_direction(ifc_entity.RefDirection) if ifc_entity.RefDirection is not None else Direction(1, 0, 0)
    axis = ifc_direction(ifc_entity.Axis) if ifc_entity.Axis is not None else Direction(0, 0, 1)
    return Axis2Placement3D(
        location=ifc_point(ifc_entity.Location),
        axis=axis,
        ref_direction=ref_dir,
    )


def axis1placement(ifc_entity) -> Axis1Placement:
    axis = ifc_direction(ifc_entity.Axis) if ifc_entity.Axis is not None else Direction(0, 0, 1)
    return Axis1Placement(
        location=ifc_point(ifc_entity.Location),
        axis=axis,
    )


def axis2placement(ifc_entity) -> Axis2Placement3D:
    ref_dir = ifc_direction(ifc_entity.RefDirection) if ifc_entity.RefDirection is not None else Direction(1, 0, 0)
    axis = ifc_direction(ifc_entity.Axis) if ifc_entity.Axis is not None else Direction(0, 0, 1)
    return Axis2Placement3D(
        location=ifc_point(ifc_entity.Location),
        axis=axis,
        ref_direction=ref_dir,
    )
