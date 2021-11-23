import logging

from ada.sections import Section


def import_section_from_ifc(ifc_elem, units="m") -> Section:
    from ada.sections.utils import interpret_section_str

    if ifc_elem.is_a("IfcIShapeProfileDef"):
        sec = Section(
            name=ifc_elem.ProfileName,
            sec_type=Section.TYPES.IPROFILE,
            h=ifc_elem.OverallDepth,
            w_top=ifc_elem.OverallWidth,
            w_btn=ifc_elem.OverallWidth,
            t_w=ifc_elem.WebThickness,
            t_ftop=ifc_elem.FlangeThickness,
            t_fbtn=ifc_elem.FlangeThickness,
            units=units,
        )
    elif ifc_elem.is_a("IfcTShapeProfileDef"):
        sec = Section(
            name=ifc_elem.ProfileName,
            sec_type=Section.TYPES.TPROFILE,
            h=ifc_elem.Depth,
            w_top=ifc_elem.FlangeWidth,
            t_w=ifc_elem.WebThickness,
            t_ftop=ifc_elem.FlangeThickness,
            units=units,
        )
    elif ifc_elem.is_a("IfcCircleHollowProfileDef"):
        sec = Section(
            name=ifc_elem.ProfileName, sec_type="TUB", r=ifc_elem.Radius, wt=ifc_elem.WallThickness, units=units
        )
    elif ifc_elem.is_a("IfcUShapeProfileDef"):
        raise NotImplementedError(f'IFC section type "{ifc_elem}" is not yet implemented')
        # sec = Section(ifc_elem.ProfileName)
    else:
        try:
            logging.warning(f'No Native support for Ifc beam object "{ifc_elem}"')
            sec, tap = interpret_section_str(ifc_elem.ProfileName)
        except ValueError as e:
            logging.debug(f'Unable to process section "{ifc_elem.ProfileName}" -> error: "{e}" ')
            sec = None
        if sec is None:
            raise NotImplementedError(f'IFC section type "{ifc_elem}" is not yet implemented')

    return sec
