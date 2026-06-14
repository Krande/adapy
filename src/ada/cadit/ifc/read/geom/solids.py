import ifcopenshell

from ada.geom import solids as geo_so
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point

from .curves import get_curve
from .placement import axis1placement, axis2placement, axis3d, ifc_direction
from .surfaces import closed_shell, get_surface


def faceted_brep(ifc_entity: ifcopenshell.entity_instance) -> geo_so.FacetedBrep:
    # Handles IfcFacetedBrep and the sibling IfcFacetedBrepWithVoids (adds inner void shells).
    voids = [closed_shell(v) for v in ifc_entity.Voids] if ifc_entity.is_a("IfcFacetedBrepWithVoids") else []
    return geo_so.FacetedBrep(outer=closed_shell(ifc_entity.Outer), voids=voids)


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


def extruded_solid_area_tapered(ifc_entity: ifcopenshell.entity_instance) -> geo_so.ExtrudedAreaSolidTapered:
    base = extruded_solid_area(ifc_entity)
    return geo_so.ExtrudedAreaSolidTapered(
        swept_area=base.swept_area,
        position=base.position,
        depth=base.depth,
        extruded_direction=base.extruded_direction,
        end_swept_area=get_surface(ifc_entity.EndSweptArea),
    )


def fixed_reference_swept_area_solid(
    ifc_entity: ifcopenshell.entity_instance,
) -> geo_so.FixedReferenceSweptAreaSolid:
    # FixedReference / StartParam / EndParam are not part of adapy's geom model (the OCC build
    # derives orientation from the directrix), so they are intentionally dropped on read.
    position = axis3d(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    return geo_so.FixedReferenceSweptAreaSolid(
        swept_area=get_surface(ifc_entity.SweptArea),
        position=position,
        directrix=get_curve(ifc_entity.Directrix),
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


def ifc_rectangular_pyramid(ifc_entity: ifcopenshell.entity_instance) -> geo_so.RectangularPyramid:
    position = axis2placement(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    return geo_so.RectangularPyramid(
        position=position,
        x_length=ifc_entity.XLength,
        y_length=ifc_entity.YLength,
        z_length=ifc_entity.Height,
    )


def ifc_block(ifc_entity: ifcopenshell.entity_instance) -> geo_so.Box:
    position = axis2placement(ifc_entity.Position) if ifc_entity.Position is not None else Axis2Placement3D()
    return geo_so.Box(
        position=position, x_length=ifc_entity.XLength, y_length=ifc_entity.YLength, z_length=ifc_entity.ZLength
    )
