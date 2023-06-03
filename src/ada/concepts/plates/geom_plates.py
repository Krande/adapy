from __future__ import annotations
from typing import TYPE_CHECKING

from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation
import ada.geom.solids as geo_so
import ada.geom.surfaces as geo_su
from ada.geom.placement import Axis2Placement3D, Direction

if TYPE_CHECKING:
    from ada.concepts.plates import Plate


def plate_to_geom(plate: Plate) -> Geometry:
    outer_curve = plate.poly.get_edges_geom()
    profile = geo_su.ArbitraryProfileDefWithVoids(geo_su.ProfileType.AREA, outer_curve, [])

    # Origin location is already included in the outer_curve definition
    place = Axis2Placement3D(axis=plate.poly.normal, ref_direction=plate.poly.xdir)
    solid = geo_so.ExtrudedAreaSolid(profile, place, plate.t, Direction(0, 0, 1))
    booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in plate.booleans]
    return Geometry(plate.guid, solid, plate.color, bool_operations=booleans)
