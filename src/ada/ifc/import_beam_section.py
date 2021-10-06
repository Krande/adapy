import logging

from ada.sections import Section


def import_section_from_ifc(ifc_elem):
    from ada.sections.utils import interpret_section_str

    try:
        sec, tap = interpret_section_str(ifc_elem.ProfileName)
    except ValueError as e:
        logging.debug(f'Unable to process section "{ifc_elem.ProfileName}" -> error: "{e}" ')
        sec = None
    if sec is None:
        if ifc_elem.is_a("IfcIShapeProfileDef"):
            sec = Section(
                name=ifc_elem.ProfileName,
                sec_type="IG",
                h=ifc_elem.OverallDepth,
                w_top=ifc_elem.OverallWidth,
                w_btn=ifc_elem.OverallWidth,
                t_w=ifc_elem.WebThickness,
                t_ftop=ifc_elem.FlangeThickness,
                t_fbtn=ifc_elem.FlangeThickness,
            )
        elif ifc_elem.is_a("IfcCircleHollowProfileDef"):
            sec = Section(
                name=ifc_elem.ProfileName,
                sec_type="TUB",
                r=ifc_elem.Radius,
                wt=ifc_elem.WallThickness,
            )
        elif ifc_elem.is_a("IfcUShapeProfileDef"):
            raise NotImplementedError(f'IFC section type "{ifc_elem}" is not yet implemented')
            # sec = Section(ifc_elem.ProfileName)
        else:
            raise NotImplementedError(f'IFC section type "{ifc_elem}" is not yet implemented')
    return sec
