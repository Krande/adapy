import ifcopenshell

from ada.core.utils import flatten
from ada.geom import surfaces as geo_su
from ada.geom.placement import Direction
from ada.geom.points import Point

from .curves import get_curve


def get_surface(ifc_entity: ifcopenshell.entity_instance) -> geo_su.SURFACE_GEOM_TYPES:
    if ifc_entity.is_a("IfcArbitraryProfileDefWithVoids") or ifc_entity.is_a("IfcArbitraryClosedProfileDef"):
        return arbitrary_closed_profile_def(ifc_entity)
    elif ifc_entity.is_a("IfcIShapeProfileDef"):
        return i_shape_profile_def(ifc_entity)
    elif ifc_entity.is_a("IfcTShapeProfileDef"):
        return t_shape_profile_def(ifc_entity)
    elif ifc_entity.is_a("IfcCircleProfileDef"):
        return circle_profile_def(ifc_entity)
    elif ifc_entity.is_a("IfcRectangleProfileDef"):
        return rectangle_profile_def(ifc_entity)
    else:
        raise NotImplementedError(f"Geometry type {ifc_entity.is_a()} not implemented")


def arbitrary_closed_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.ArbitraryProfileDef:
    outer_curve = get_curve(ifc_entity.OuterCurve)

    inner_curves = []
    if hasattr(ifc_entity, "InnerCurves"):
        for inner_curve in ifc_entity.InnerCurves:
            inner_curves.append(get_curve(inner_curve))

    return geo_su.ArbitraryProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        outer_curve=outer_curve,
        inner_curves=inner_curves,
    )


def i_shape_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.IShapeProfileDef:
    return geo_su.IShapeProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        overall_width=ifc_entity.OverallWidth,
        overall_depth=ifc_entity.OverallDepth,
        web_thickness=ifc_entity.WebThickness,
        flange_thickness=ifc_entity.FlangeThickness,
        fillet_radius=ifc_entity.FilletRadius,
        flange_edge_radius=ifc_entity.FlangeEdgeRadius,
        flange_slope=ifc_entity.FlangeSlope,
    )


def t_shape_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.TShapeProfileDef:
    return geo_su.TShapeProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        depth=ifc_entity.Depth,
        flange_width=ifc_entity.FlangeWidth,
        web_thickness=ifc_entity.WebThickness,
        flange_thickness=ifc_entity.FlangeThickness,
        fillet_radius=ifc_entity.FilletRadius,
        flange_edge_radius=ifc_entity.FlangeEdgeRadius,
        web_edge_radius=ifc_entity.WebEdgeRadius,
        web_slope=ifc_entity.WebSlope,
        flange_slope=ifc_entity.FlangeSlope,
    )


def circle_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.CircleProfileDef:
    return geo_su.CircleProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        radius=ifc_entity.Radius,
    )


def triangulated_face_set(ifc_entity: ifcopenshell.entity_instance) -> geo_su.TriangulatedFaceSet:
    return geo_su.TriangulatedFaceSet(
        coordinates=[Point(*x) for x in ifc_entity.Coordinates.CoordList],
        indices=flatten(ifc_entity.CoordIndex),
        normals=[Direction(*x) for x in ifc_entity.Normals],
    )


def rectangle_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.RectangleProfileDef:
    return geo_su.RectangleProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        x_dim=ifc_entity.XDim,
        y_dim=ifc_entity.YDim,
    )
