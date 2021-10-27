import logging

import numpy as np

from ada import Section
from ada.config import Settings
from ada.sections.categories import SectionCat

from .utils import create_guid, create_ifcindexpolyline, create_ifcpolyline


def export_beam_section(section: Section):
    a = section.parent.parent.get_assembly()
    f = a.ifc_file
    sec_props = dict(ProfileType="AREA", ProfileName=section.name)
    section_profile = section.get_section_profile(True)
    if SectionCat.is_i_profile(section.type):
        if Settings.use_param_profiles is False:
            polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)

            ifc_sec_type = "IfcArbitraryClosedProfileDef"
            sec_props.update(dict(OuterCurve=polyline))
        else:
            if SectionCat.is_strong_axis_symmetric(section) is False:
                logging.error(
                    "Note! Not using IfcAsymmetricIShapeProfileDef as it is not supported by ifcopenshell v IFC4"
                )
                # ifc_sec_type = "IfcAsymmetricIShapeProfileDef"
                # sec_props.update(
                #     dict(
                #         TopFlangeWidth=self.w_top,
                #         BottomFlangeWidth=self.w_btn,
                #         OverallDepth=self.h,
                #         WebThickness=self.t_w,
                #         TopFlangeThickness=self.t_ftop,
                #         BottomFlangeThickness=self.t_fbtn,
                #     )
                # )

            ifc_sec_type = "IfcIShapeProfileDef"
            sec_props.update(
                dict(
                    OverallWidth=section.w_top,
                    OverallDepth=section.h,
                    WebThickness=section.t_w,
                    FlangeThickness=section.t_ftop,
                )
            )

    elif SectionCat.is_angular(section.type):
        points = [f.createIfcCartesianPoint(p) for p in section_profile.outer_curve.points2d]
        ifc_polyline = f.createIfcPolyLine(points)
        ifc_sec_type = "IfcArbitraryClosedProfileDef"
        sec_props.update(dict(OuterCurve=ifc_polyline))

        if Settings.use_param_profiles is True:
            logging.debug(f'Export of "{section.type}" profile to parametric IFC profile is not yet added')

    elif SectionCat.is_box_profile(section.type):
        ot_disc = section_profile.outer_curve.points2d
        in_disc = section_profile.inner_curve.points2d
        outer_points = [f.createIfcCartesianPoint(p) for p in ot_disc + [ot_disc[0]]]
        inner_points = [f.createIfcCartesianPoint(p) for p in in_disc + [in_disc[0]]]
        inner_curve = f.createIfcPolyLine(inner_points)
        outer_curve = f.createIfcPolyLine(outer_points)
        ifc_sec_type = "IfcArbitraryProfileDefWithVoids"
        sec_props.update(dict(OuterCurve=outer_curve, InnerCurves=[inner_curve]))

        if Settings.use_param_profiles is True:
            logging.debug(f'Export of "{section.type}" profile to parametric IFC profile is not yet added')

    elif section.type in SectionCat.circular:
        ifc_sec_type = "IfcCircleProfileDef"
        sec_props.update(dict(Radius=section.r))
    elif section.type in SectionCat.tubular:
        ifc_sec_type = "IfcCircleHollowProfileDef"
        sec_props.update(dict(Radius=section.r, WallThickness=section.wt))
    elif section.type in SectionCat.general:
        logging.error("Note! Creating a Circle profile from general section (just for visual inspection as of now)")
        r = np.sqrt(section.properties.Ax / np.pi)
        ifc_sec_type = "IfcCircleProfileDef"
        sec_props.update(dict(Radius=r))
    elif section.type in SectionCat.flatbar:
        polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)
        ifc_sec_type = "IfcArbitraryClosedProfileDef"
        sec_props.update(dict(OuterCurve=polyline))

        if Settings.use_param_profiles is True:
            logging.debug(f'Export of "{section.type}" profile to parametric IFC profile is not yet added')

    elif section.type in SectionCat.channels:
        if Settings.use_param_profiles is False:
            polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)
            ifc_sec_type = "IfcArbitraryClosedProfileDef"
            sec_props.update(dict(OuterCurve=polyline))
        else:
            ifc_sec_type = "IfcUShapeProfileDef"
            sec_props.update(
                dict(
                    Depth=section.h, FlangeWidth=section.w_top, WebThickness=section.t_w, FlangeThickness=section.t_ftop
                )
            )
    elif section.type == "poly":
        opoly = section.poly_outer
        opoints = [(float(n[0]), float(n[1]), float(n[2])) for n in opoly.seg_global_points]
        opolyline = create_ifcindexpolyline(f, opoints, opoly.seg_index)
        if section.poly_inner is None:
            ifc_sec_type = "IfcArbitraryClosedProfileDef"
            sec_props.update(dict(OuterCurve=opolyline))
        else:
            ipoly = section.poly_inner
            ipoints = [(float(n[0]), float(n[1]), float(n[2])) for n in ipoly.seg_global_points]
            ipolyline = create_ifcindexpolyline(f, ipoints, ipoly.seg_index)
            ifc_sec_type = "IfcArbitraryProfileDefWithVoids"
            sec_props.update(dict(OuterCurve=opolyline, InnerCurves=[ipolyline]))
    else:
        raise ValueError(f'Have yet to implement section type "{section.type}"')

    if section.name is None:
        raise ValueError("Name cannot be None!")

    profile = f.create_entity(ifc_sec_type, **sec_props)

    beamtype = f.create_entity(
        "IfcBeamType",
        create_guid(),
        a.user.to_ifc(),
        section.name,
        section.sec_str,
        None,
        None,
        None,
        None,
        None,
        "BEAM",
    )
    return profile, beamtype
