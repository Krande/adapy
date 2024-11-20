from __future__ import annotations

from dataclasses import dataclass

import ifcopenshell
import numpy as np

from ada.config import Config, logger
from ada.core.utils import to_real
from ada.sections.categories import SectionCat
from ada.sections.concept import Section

from ..utils import create_ifcindexpolyline, create_ifcpolyline


class UnrecognizedSectionType(Exception):
    pass


def get_profile_class(section: Section) -> ProfileBase:
    st = Section.TYPES

    if section.type == st.IPROFILE:
        return IProfile(section)
    elif section.type == st.ANGULAR:
        return AngularProfile(section)
    elif section.type == st.BOX:
        return BoxProfile(section)
    elif section.type == st.TPROFILE:
        return TProfile(section)
    elif section.type == st.CHANNEL:
        return ChannelProfile(section)
    elif section.type == st.CIRCULAR:
        return CircularProfile(section)
    elif section.type == st.TUBULAR:
        return TubularProfile(section)
    elif section.type == st.GENERAL:
        return GeneralProfile(section)
    elif section.type == st.FLATBAR:
        return FlatBarProfile(section)
    elif section.type == st.POLY:
        return PolyProfile(section)
    else:
        raise UnrecognizedSectionType(f"Type -> {section.type}")


def export_beam_section_profile_def(section: Section):
    if section.parent is None or section.parent.parent is None:
        raise ValueError("Lacking parent")

    if section.name is None:
        raise ValueError("Name cannot be None!")

    a = section.parent.parent.get_assembly()
    f = a.ifc_store.f

    sec_props = dict(ProfileType="AREA", ProfileName=section.name)

    section_profile_instance = get_profile_class(section)

    sec_props_input = section_profile_instance.get_ifc_props(f)
    sec_props.update(sec_props_input)

    ifc_sec_type = section_profile_instance.get_ifc_type()

    profile = f.create_entity(ifc_sec_type, **sec_props)

    return profile


@dataclass
class ProfileBase:
    section: Section

    def get_ifc_type(self) -> str: ...

    def get_ifc_props(self, f: ifcopenshell.file) -> dict: ...


@dataclass
class IProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        if Config().general_force_param_profiles is False:
            return "IfcArbitraryClosedProfileDef"
        else:
            return "IfcIShapeProfileDef"

    def get_ifc_props(self, f: ifcopenshell.file) -> dict:
        section = self.section
        if Config().general_force_param_profiles is False:
            section_profile = section.get_section_profile(True)
            polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)

            sec_props = dict(OuterCurve=polyline)
        else:
            if SectionCat.is_strong_axis_symmetric(section) is False:
                logger.info("Note! IfcAsymmetricIShapeProfileDef as it is not supported by ifcopenshell v IFC4")
            sec_props = dict(
                OverallWidth=section.w_top,
                OverallDepth=section.h,
                WebThickness=section.t_w,
                FlangeThickness=section.t_ftop,
            )
        return sec_props


@dataclass
class TProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        if Config().general_force_param_profiles is False:
            return "IfcArbitraryClosedProfileDef"
        else:
            return "IfcTShapeProfileDef"

    def get_ifc_props(self, f) -> dict:
        section = self.section
        if Config().general_force_param_profiles is False:
            section_profile = section.get_section_profile(True)
            polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)

            sec_props = dict(OuterCurve=polyline)
        else:
            if SectionCat.is_strong_axis_symmetric(section) is False:
                logger.info(
                    "Note! Not using IfcAsymmetricIShapeProfileDef as it is not supported by ifcopenshell v IFC4"
                )
            sec_props = dict(
                FlangeWidth=section.w_top,
                Depth=section.h,
                WebThickness=section.t_w,
                FlangeThickness=section.t_ftop,
            )
        return sec_props


@dataclass
class AngularProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        return "IfcArbitraryClosedProfileDef"

    def get_ifc_props(self, f: ifcopenshell.file) -> dict:
        section = self.section
        if Config().general_force_param_profiles is True:
            logger.debug(f'Export of "{section.type}" profile to parametric IFC profile is not yet added')

        section_profile = section.get_section_profile(True)
        points2d = section_profile.outer_curve.points2d
        if not points2d[0].is_equal(points2d[-1]):
            points2d.append(points2d[0])

        points = [f.create_entity("IfcCartesianPoint", Coordinates=to_real(p)) for p in points2d]
        ifc_polyline = f.create_entity("IfcPolyLine", Points=points)

        return dict(OuterCurve=ifc_polyline)


@dataclass
class BoxProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        return "IfcArbitraryProfileDefWithVoids"

    def get_ifc_props(self, f: ifcopenshell.file) -> dict:
        section = self.section
        if Config().general_force_param_profiles is True:
            logger.debug(f'Export of "{section.type}" profile to parametric IFC profile is not yet added')
        section_profile = section.get_section_profile(True)
        ot_disc = section_profile.outer_curve.points2d
        in_disc = section_profile.inner_curve.points2d
        outer_points = [f.createIfcCartesianPoint(to_real(p)) for p in ot_disc + [ot_disc[0]]]
        inner_points = [f.createIfcCartesianPoint(to_real(p)) for p in in_disc + [in_disc[0]]]
        inner_curve = f.createIfcPolyLine(inner_points)
        outer_curve = f.createIfcPolyLine(outer_points)
        return dict(OuterCurve=outer_curve, InnerCurves=[inner_curve])


@dataclass
class CircularProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        return "IfcCircleProfileDef"

    def get_ifc_props(self, f: ifcopenshell.file) -> dict:
        return dict(Radius=self.section.r)


@dataclass
class TubularProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        return "IfcCircleHollowProfileDef"

    def get_ifc_props(self, f: ifcopenshell.file) -> dict:
        section = self.section
        return dict(Radius=section.r, WallThickness=section.wt)


@dataclass
class GeneralProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        return "IfcCircleProfileDef"

    def get_ifc_props(self, f: ifcopenshell.file) -> dict:
        logger.warning("Note! Creating a Circle profile from general section (just for visual inspection as of now)")
        r = np.sqrt(self.section.properties.Ax / np.pi)
        return dict(Radius=r)


@dataclass
class FlatBarProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        return "IfcArbitraryClosedProfileDef"

    def get_ifc_props(self, f: ifcopenshell.file) -> dict:
        section = self.section
        if Config().general_force_param_profiles is True:
            logger.debug(f'Export of "{section.type}" profile to parametric IFC profile is not yet added')
        section_profile = section.get_section_profile(True)
        polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)
        return dict(OuterCurve=polyline)


@dataclass
class ChannelProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        if Config().general_force_param_profiles is False:
            return "IfcArbitraryClosedProfileDef"
        else:
            return "IfcUShapeProfileDef"

    def get_ifc_props(self, f: ifcopenshell.file) -> dict:
        section = self.section
        if Config().general_force_param_profiles is False:
            section_profile = section.get_section_profile(True)
            polyline = create_ifcpolyline(f, section_profile.outer_curve.points2d)

            props = dict(OuterCurve=polyline)
        else:
            props = dict(
                Depth=section.h, FlangeWidth=section.w_top, WebThickness=section.t_w, FlangeThickness=section.t_ftop
            )
        return props


@dataclass
class PolyProfile(ProfileBase):
    def get_ifc_type(self) -> str:
        if self.section.poly_inner is None:
            return "IfcArbitraryClosedProfileDef"
        else:
            return "IfcArbitraryProfileDefWithVoids"

    def get_ifc_props(self, f: ifcopenshell.file) -> dict:
        section = self.section
        opoly = section.poly_outer
        opoints = [(float(n[0]), float(n[1]), float(n[2])) for n in opoly.seg_global_points]
        opolyline = create_ifcindexpolyline(f, opoints, opoly.seg_index)
        if section.poly_inner is None:
            props = dict(OuterCurve=opolyline)
        else:
            ipoly = section.poly_inner
            ipoints = [(float(n[0]), float(n[1]), float(n[2])) for n in ipoly.seg_global_points]
            ipolyline = create_ifcindexpolyline(f, ipoints, ipoly.seg_index)

            props = dict(OuterCurve=opolyline, InnerCurves=[ipolyline])

        return props
