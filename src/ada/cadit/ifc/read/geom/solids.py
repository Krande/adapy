import ifcopenshell

from ada.geom import solids as geo_so
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point

from .curves import get_curve
from .placement import axis1placement, axis2placement, axis3d, ifc_direction
from .surfaces import get_surface


def swept_disk_solid(ifc_entity: ifcopenshell.entity_instance) -> geo_so.SweptDiskSolid:
    # Covers the IfcSweptDiskSolidPolygonal subtype (which adds a FilletRadius).
    fillet_radius = getattr(ifc_entity, "FilletRadius", None)
    return geo_so.SweptDiskSolid(
        directrix=get_curve(ifc_entity.Directrix),
        radius=ifc_entity.Radius,
        inner_radius=ifc_entity.InnerRadius,
        start_param=ifc_entity.StartParam,
        end_param=ifc_entity.EndParam,
        fillet_radius=fillet_radius,
    )


def ifc_sphere(ifc_entity: ifcopenshell.entity_instance) -> geo_so.Sphere:
    center = Point(*ifc_entity.Position.Location.Coordinates)
    return geo_so.Sphere(center=center, radius=ifc_entity.Radius)


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


def ifc_cylinder(ifc_entity: ifcopenshell.entity_instance) -> geo_so.Cylinder:
    position = axis2placement(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    return geo_so.Cylinder(position=position, radius=ifc_entity.Radius, height=ifc_entity.Height)


def ifc_cone(ifc_entity: ifcopenshell.entity_instance) -> geo_so.Cone:
    position = axis2placement(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    return geo_so.Cone(position=position, bottom_radius=ifc_entity.BottomRadius, height=ifc_entity.Height)


def ifc_block(ifc_entity: ifcopenshell.entity_instance) -> geo_so.Box:
    position = axis2placement(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    return geo_so.Box(
        position=position, x_length=ifc_entity.XLength, y_length=ifc_entity.YLength, z_length=ifc_entity.ZLength
    )
