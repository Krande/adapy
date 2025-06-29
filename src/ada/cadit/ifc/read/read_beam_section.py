from ada.base.units import Units
from ada.config import logger
from ada.sections import Section


def import_section_from_ifc(profile_def, units=Units.M) -> Section:
    """Takes any subclass of ProfileDef"""
    from ada.sections.string_to_section import interpret_section_str

    if profile_def.is_a("IfcIShapeProfileDef"):
        sec = Section(
            name=profile_def.ProfileName,
            sec_type=Section.TYPES.IPROFILE,
            h=profile_def.OverallDepth,
            w_top=profile_def.OverallWidth,
            w_btn=profile_def.OverallWidth,
            t_w=profile_def.WebThickness,
            t_ftop=profile_def.FlangeThickness,
            t_fbtn=profile_def.FlangeThickness,
            units=units,
            sec_str=profile_def.ProfileName,
        )
    elif profile_def.is_a("IfcTShapeProfileDef"):
        sec = Section(
            name=profile_def.ProfileName,
            sec_type=Section.TYPES.TPROFILE,
            h=profile_def.Depth,
            w_top=profile_def.FlangeWidth,
            t_w=profile_def.WebThickness,
            t_ftop=profile_def.FlangeThickness,
            units=units,
        )
    elif profile_def.is_a("IfcCircleHollowProfileDef"):
        sec = Section(
            name=profile_def.ProfileName,
            sec_type="TUB",
            r=profile_def.Radius,
            wt=profile_def.WallThickness,
            units=units,
        )
    elif profile_def.is_a("IfcUShapeProfileDef"):
        raise NotImplementedError(f'IFC section type "{profile_def}" is not yet implemented')
        # sec = Section(ifc_elem.ProfileName)
    else:
        try:
            logger.info(f'No Native support for Ifc beam "{profile_def=}"')
            sec, tap = interpret_section_str(profile_def.ProfileName)
        except ValueError as e:
            logger.warning(f'Unable to process section "{profile_def.ProfileName}" -> error: "{e}" ')
            sec = None
        if sec is None:
            raise NotImplementedError(f'IFC section type "{profile_def}" is not yet implemented')

    return sec
