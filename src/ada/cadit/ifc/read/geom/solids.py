import ifcopenshell

from ada.geom import solids as geo_so
from ada.geom.placement import Axis2Placement3D

from .placement import axis3d, ifc_direction
from .surfaces import get_surface


def extruded_solid_area(ifc_entity: ifcopenshell.entity_instance) -> geo_so.ExtrudedAreaSolid:
    extrude_dir = ifc_direction(ifc_entity.ExtrudedDirection)
    position = axis3d(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    swept_area = get_surface(ifc_entity.SweptArea)
    return geo_so.ExtrudedAreaSolid(
        swept_area=swept_area, position=position, depth=ifc_entity.Depth, extruded_direction=extrude_dir
    )
