import logging
from typing import Tuple

import numpy as np

from ada import Section
from ada.config import Settings
from ada.sections.categories import SectionCat

from ..utils import create_guid, create_ifcindexpolyline, create_ifcpolyline


def export_beam_section(section: Section):
    if section.parent is None or section.parent.parent is None:
        raise ValueError("Lack")
    a = section.parent.parent.get_assembly()
    f = a.ifc_file
    sec_props = dict(ProfileType="AREA", ProfileName=section.name)
    section_profile = section.get_section_profile(True)
    gen_type = SectionCat.get_shape_type(section)

    st = Section.TYPES
    sec_map = {
        st.IPROFILE: write_iprofile,
        st.ANGULAR: write_angular,
        st.BOX: write_box,
        st.TPROFILE: write_tprofile,
        st.CHANNEL: write_channel,
        st.CIRCULAR: write_circular,
        st.TUBULAR: write_tubular,
        st.GENERAL: write_general,
        st.FLATBAR: write_flatbar,
        st.POLY: write_poly,
    }

    section_props = sec_map.get(gen_type, None)

    if section_props is None:
        raise ValueError(f'Have yet to implement section type "{section.type}"')

    sec_props_input, ifc_sec_type = section_props(f, section, section_profile)
    sec_props.update(sec_props_input)

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


def write_iprofile(f, section, section_profile) -> Tuple[dict, str]:
    if Settings.use_param_profiles is False:
        polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)

        ifc_sec_type = "IfcArbitraryClosedProfileDef"
        sec_props = dict(OuterCurve=polyline), ifc_sec_type
    else:
        if SectionCat.is_strong_axis_symmetric(section) is False:
            logging.warning("Note! IfcAsymmetricIShapeProfileDef as it is not supported by ifcopenshell v IFC4")

        ifc_sec_type = "IfcIShapeProfileDef"

        sec_props = dict(
            OverallWidth=section.w_top,
            OverallDepth=section.h,
            WebThickness=section.t_w,
            FlangeThickness=section.t_ftop,
        )
    return sec_props, ifc_sec_type


def write_tprofile(f, section, section_profile) -> Tuple[dict, str]:
    if Settings.use_param_profiles is False:
        polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)

        ifc_sec_type = "IfcArbitraryClosedProfileDef"
        sec_props = dict(OuterCurve=polyline), ifc_sec_type
    else:
        if SectionCat.is_strong_axis_symmetric(section) is False:
            logging.warning(
                "Note! Not using IfcAsymmetricIShapeProfileDef as it is not supported by ifcopenshell v IFC4"
            )
        ifc_sec_type = "IfcTShapeProfileDef"

        sec_props = dict(
            FlangeWidth=section.w_top,
            Depth=section.h,
            WebThickness=section.t_w,
            FlangeThickness=section.t_ftop,
        )
    return sec_props, ifc_sec_type


def write_angular(f, section, section_profile):
    if Settings.use_param_profiles is True:
        logging.debug(f'Export of "{section.type}" profile to parametric IFC profile is not yet added')

    points = [f.createIfcCartesianPoint(p) for p in section_profile.outer_curve.points2d]
    ifc_polyline = f.createIfcPolyLine(points)
    return dict(OuterCurve=ifc_polyline), "IfcArbitraryClosedProfileDef"


def write_box(f, section, section_profile):
    if Settings.use_param_profiles is True:
        logging.debug(f'Export of "{section.type}" profile to parametric IFC profile is not yet added')

    ot_disc = section_profile.outer_curve.points2d
    in_disc = section_profile.inner_curve.points2d
    outer_points = [f.createIfcCartesianPoint(p) for p in ot_disc + [ot_disc[0]]]
    inner_points = [f.createIfcCartesianPoint(p) for p in in_disc + [in_disc[0]]]
    inner_curve = f.createIfcPolyLine(inner_points)
    outer_curve = f.createIfcPolyLine(outer_points)
    return dict(OuterCurve=outer_curve, InnerCurves=[inner_curve]), "IfcArbitraryProfileDefWithVoids"


def write_circular(f, section, section_profile):
    return dict(Radius=section.r), "IfcCircleProfileDef"


def write_tubular(f, section, section_profile):
    return dict(Radius=section.r, WallThickness=section.wt), "IfcCircleHollowProfileDef"


def write_general(f, section, section_profile):
    logging.warning("Note! Creating a Circle profile from general section (just for visual inspection as of now)")
    r = np.sqrt(section.properties.Ax / np.pi)
    return dict(Radius=r), "IfcCircleProfileDef"


def write_flatbar(f, section, section_profile):
    if Settings.use_param_profiles is True:
        logging.debug(f'Export of "{section.type}" profile to parametric IFC profile is not yet added')
    polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)
    return dict(OuterCurve=polyline), "IfcArbitraryClosedProfileDef"


def write_channel(f, section, section_profile):
    if Settings.use_param_profiles is False:
        polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)
        ifc_sec_type = "IfcArbitraryClosedProfileDef"
        props = dict(OuterCurve=polyline)
    else:
        ifc_sec_type = "IfcUShapeProfileDef"
        props = dict(
            Depth=section.h, FlangeWidth=section.w_top, WebThickness=section.t_w, FlangeThickness=section.t_ftop
        )
    return props, ifc_sec_type


def write_poly(f, section, section_profile):
    opoly = section.poly_outer
    opoints = [(float(n[0]), float(n[1]), float(n[2])) for n in opoly.seg_global_points]
    opolyline = create_ifcindexpolyline(f, opoints, opoly.seg_index)
    if section.poly_inner is None:
        ifc_sec_type = "IfcArbitraryClosedProfileDef"
        props = dict(OuterCurve=opolyline)
    else:
        ipoly = section.poly_inner
        ipoints = [(float(n[0]), float(n[1]), float(n[2])) for n in ipoly.seg_global_points]
        ipolyline = create_ifcindexpolyline(f, ipoints, ipoly.seg_index)
        ifc_sec_type = "IfcArbitraryProfileDefWithVoids"
        props = dict(OuterCurve=opolyline, InnerCurves=[ipolyline])

    return props, ifc_sec_type
