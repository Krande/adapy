import ifcopenshell

from ada.geom import solids as geo_so
from ada.geom.placement import Axis2Placement3D

from .placement import axis1placement, axis2placement, axis3d, ifc_direction
from .surfaces import get_surface


def extruded_solid_area(ifc_entity: ifcopenshell.entity_instance) -> geo_so.ExtrudedAreaSolid:
    extrude_dir = ifc_direction(ifc_entity.ExtrudedDirection)
    position = axis3d(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    swept_area = get_surface(ifc_entity.SweptArea)
    return geo_so.ExtrudedAreaSolid(
        swept_area=swept_area, position=position, depth=ifc_entity.Depth, extruded_direction=extrude_dir
    )


def revolved_solid_area(ifc_entity: ifcopenshell.entity_instance) -> geo_so.RevolvedAreaSolid:
    revolve_axis = axis1placement(ifc_entity.Axis)
    position = axis3d(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    swept_area = get_surface(ifc_entity.SweptArea)
    return geo_so.RevolvedAreaSolid(
        swept_area=swept_area,
        position=position,
        axis=revolve_axis,
        angle=ifc_entity.Angle,
    )


def ifc_block(ifc_entity: ifcopenshell.entity_instance) -> geo_so.Box:
    position = axis2placement(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    return geo_so.Box(
        position=position, x_length=ifc_entity.XLength, y_length=ifc_entity.YLength, z_length=ifc_entity.ZLength
    )
