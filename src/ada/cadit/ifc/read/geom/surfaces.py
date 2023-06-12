import ifcopenshell


from ada.geom import surfaces as geo_su
from .curves import get_curve


def get_surface(ifc_entity: ifcopenshell.entity_instance) -> geo_su.SURFACE_GEOM_TYPES:
    if ifc_entity.is_a("IfcArbitraryProfileDefWithVoids") or ifc_entity.is_a("IfcArbitraryClosedProfileDef"):
        return arbitrary_closed_profile_def(ifc_entity)
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
