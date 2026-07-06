from ada.base.units import Units
from ada.config import logger
from ada.sections import Section


def import_section_from_ifc(profile_def, units=Units.M) -> Section:
    """Takes any subclass of ProfileDef and returns an adapy Section.

    Resolution order:
      1. adapy parameter bag (IfcProfileProperties) -> exact parametric section.
      2. Native parametric IFC profile defs (I/T/circle-hollow).
      3. Polyline/arbitrary geometry -> recognise parametric or keep as POLY.
      4. Last resort: parse the profile name string.
    """
    from ada.cadit.ifc.sections_props import (
        read_profile_section_params,
        section_from_param_dict,
    )
    from ada.sections.string_to_section import interpret_section_str

    name = getattr(profile_def, "ProfileName", None)

    # 1. Exact round-trip via the adapy parameter bag (works for every type).
    params = read_profile_section_params(profile_def)
    if params is not None:
        return section_from_param_dict(name, params, units)

    # 2. Native parametric profile defs.
    if profile_def.is_a("IfcIShapeProfileDef"):
        return Section(
            name=name,
            sec_type=Section.TYPES.IPROFILE,
            h=profile_def.OverallDepth,
            w_top=profile_def.OverallWidth,
            w_btn=profile_def.OverallWidth,
            t_w=profile_def.WebThickness,
            t_ftop=profile_def.FlangeThickness,
            t_fbtn=profile_def.FlangeThickness,
            # Flange-root fillet radius (optional in IFC). Stored in the otherwise-unused ``r``
            # for I-sections so iprofiles() rounds the four web/flange junctions instead of
            # drawing sharp corners.
            r=profile_def.FilletRadius,
            units=units,
            sec_str=name,
        )
    elif profile_def.is_a("IfcTShapeProfileDef"):
        # Adapy's TPROFILE convention (see string_to_section): the absent bottom
        # flange is encoded collapsed-onto-the-web (w_btn = t_w, t_fbtn = t_ftop),
        # not None — the section writers (e.g. Genie XML's unsymmetrical_i_section)
        # do arithmetic on these fields.
        return Section(
            name=name,
            sec_type=Section.TYPES.TPROFILE,
            h=profile_def.Depth,
            w_top=profile_def.FlangeWidth,
            w_btn=profile_def.WebThickness,
            t_w=profile_def.WebThickness,
            t_ftop=profile_def.FlangeThickness,
            t_fbtn=profile_def.FlangeThickness,
            units=units,
        )
    elif profile_def.is_a("IfcUShapeProfileDef"):
        # Mirrors ChannelProfile.get_ifc_props (write_sections): Depth=h,
        # FlangeWidth=w_top, WebThickness=t_w, FlangeThickness=t_ftop. UNP
        # channels are symmetric so both flanges share width/thickness.
        return Section(
            name=name,
            sec_type=Section.TYPES.CHANNEL,
            h=profile_def.Depth,
            w_top=profile_def.FlangeWidth,
            w_btn=profile_def.FlangeWidth,
            t_w=profile_def.WebThickness,
            t_ftop=profile_def.FlangeThickness,
            t_fbtn=profile_def.FlangeThickness,
            units=units,
        )
    elif profile_def.is_a("IfcCircleHollowProfileDef"):
        return Section(
            name=name,
            sec_type="TUB",
            r=profile_def.Radius,
            wt=profile_def.WallThickness,
            units=units,
        )

    # 3. Polyline / arbitrary geometry -> reconstruct from the curves.
    if profile_def.is_a("IfcArbitraryClosedProfileDef") or profile_def.is_a("IfcArbitraryProfileDefWithVoids"):
        sec = _section_from_arbitrary_profile(profile_def, name, units)
        if sec is not None:
            return sec

    # 4. Last resort: interpret the profile name string.
    try:
        logger.info(f'No native/geometry support for Ifc beam "{profile_def=}", trying name parse')
        sec, _ = interpret_section_str(profile_def.ProfileName)
    except Exception as e:
        logger.warning(f'Unable to process section "{name}" -> error: "{e}"')
        sec = None
    if sec is None:
        raise NotImplementedError(f'IFC section type "{profile_def}" is not yet implemented')

    return sec


def _section_from_arbitrary_profile(profile_def, name, units) -> Section | None:
    """Reconstruct a Section from an arbitrary (polyline) profile def."""
    from ada.cadit.ifc.read.geom.surfaces import arbitrary_closed_profile_def
    from ada.sections.from_geometry import section_from_polyline

    try:
        apd = arbitrary_closed_profile_def(profile_def)
        outer = _curve_to_points2d(apd.outer_curve)
        inner_curves = [_curve_to_points2d(c) for c in apd.inner_curves]
    except NotImplementedError as e:
        logger.warning(f'Unable to read arbitrary profile geometry for "{name}" -> {e}')
        return None

    inner = inner_curves[0] if inner_curves else None
    return section_from_polyline(outer, inner, name=name, units=units)


def _curve_to_points2d(curve) -> list[tuple[float, float]]:
    from ada.geom import curves as geo_cu

    if isinstance(curve, geo_cu.PolyLine):
        raw = [tuple(p) for p in curve.points]
    elif isinstance(curve, geo_cu.IndexedPolyCurve):
        raw = curve.get_points()
    else:
        raise NotImplementedError(f"Unsupported profile curve geometry {type(curve).__name__}")

    return [(float(p[0]), float(p[1])) for p in raw]
