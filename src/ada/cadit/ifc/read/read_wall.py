from __future__ import annotations

from typing import TYPE_CHECKING

from ada import Placement, Wall
from ada.config import logger

from .exceptions import NoIfcAxesAttachedError
from .geom.geom_reader import get_product_definitions
from .geom.placement import axis3d
from .reader_utils import get_axis_polyline_points_from_product, get_ifc_property_sets

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def import_ifc_wall(ifc_elem, name, ifc_store: IfcStore) -> Wall:
    """Reconstruct a Wall from an IfcWall written by write_ifc_wall.

    Points come from the Axis (Curve2D) polyline, height from the body extrusion depth, and
    thickness/offset from the property set the writer attaches (they can't be recovered
    unambiguously from the extruded footprint)."""
    points = [tuple(p) for p in get_axis_polyline_points_from_product(ifc_elem)]
    if len(points) < 2:
        raise NoIfcAxesAttachedError(f"IfcWall {name} has no usable Axis polyline")

    height = None
    for body in get_product_definitions(ifc_elem):
        depth = getattr(body, "depth", None)
        if depth is not None:
            height = float(depth)
            break
    if height is None:
        raise NoIfcAxesAttachedError(f"IfcWall {name} has no extruded body to read the height from")

    props = get_ifc_property_sets(ifc_elem).get("Properties", {})
    thickness = props.get("thickness")
    if thickness is None:
        logger.warning(f"IfcWall {name} has no 'thickness' property; defaulting to 0.1")
        thickness = 0.1
    offset = props.get("offset", 0.0)

    place = Placement.from_axis3d(axis3d(ifc_elem.ObjectPlacement.RelativePlacement))

    return Wall(
        name,
        points,
        height=float(height),
        thickness=float(thickness),
        offset=float(offset),
        placement=place,
        guid=ifc_elem.GlobalId,
        units=ifc_store.assembly.units,
    )
