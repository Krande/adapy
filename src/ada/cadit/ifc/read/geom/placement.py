import numpy as np

from ada import Direction, Point
from ada.api.transforms import Placement
from ada.geom.placement import Axis1Placement, Axis2Placement3D


def placement_from_ifc_4x4(matrix) -> Placement:
    """Build a Placement from an IFC world transform (``get_local_placement`` / a column-major
    ``IfcObjectPlacement`` 4x4), whose local axes are the rotation COLUMNS.

    ``Placement`` stores its rotation as ROWS (xdir/ydir/zdir) and ``get_matrix4x4`` — which the
    scene/tessellation applies as ``M@p`` — rebuilds it as rows, so ``from_4x4_matrix(M)`` yields
    a placement whose ``get_matrix4x4() == M.T``. That is invisible for a symmetric rotation but
    renders an axis-swapping placement along the wrong world axis (beam-standard-case.ifc,
    box_rotated.ifc). Transpose the rotation here so the scene applies the intended matrix.
    """
    m = np.asarray(matrix, dtype=float).copy()
    m[:3, :3] = m[:3, :3].T
    return Placement.from_4x4_matrix(m)


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


def axis2d_as_3d(ifc_entity) -> Axis2Placement3D:
    """IfcAxis2Placement2D (Location + optional RefDirection, no Axis) lifted into the z=0 plane,
    so 2D curve placements (alignment line/circle/clothoid parents) reuse the 3D geom types."""
    loc = ifc_entity.Location.Coordinates
    rd = ifc_entity.RefDirection.DirectionRatios if ifc_entity.RefDirection is not None else (1.0, 0.0)
    return Axis2Placement3D(
        location=Point(float(loc[0]), float(loc[1]), 0.0),
        axis=Direction(0, 0, 1),
        ref_direction=Direction(float(rd[0]), float(rd[1]), 0.0),
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
