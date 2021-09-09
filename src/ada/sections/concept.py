import logging

import numpy as np

from ada.base.non_phyical_objects import Backend
from ada.concepts.curves import CurvePoly
from ada.config import Settings
from ada.ifc.utils import create_guid

from .categories import SectionCat
from .properties import GeneralProperties


class Section(Backend):
    def __init__(
        self,
        name,
        sec_type=None,
        h=None,
        w_top=None,
        w_btn=None,
        t_w=None,
        t_ftop=None,
        t_fbtn=None,
        r=None,
        wt=None,
        sec_id=None,
        parent=None,
        sec_str=None,
        from_str=None,
        outer_poly=None,
        inner_poly=None,
        genprops=None,
        metadata=None,
        units="m",
        ifc_elem=None,
        guid=None,
    ):
        super(Section, self).__init__(name, guid, metadata, units, ifc_elem=ifc_elem)
        self._type = sec_type
        self._h = h
        self._w_top = w_top
        self._w_btn = w_btn
        self._t_w = t_w
        self._t_ftop = t_ftop
        self._t_fbtn = t_fbtn
        self._r = r
        self._wt = wt
        self._id = sec_id
        self._outer_poly = outer_poly
        self._inner_poly = inner_poly
        self._sec_str = sec_str
        self._parent = parent

        self._ifc_profile = None
        self._ifc_beam_type = None

        if ifc_elem is not None:
            props = self._import_from_ifc_profile(ifc_elem)
            self.__dict__.update(props.__dict__)

        if from_str is not None:
            from ada.sections.utils import interpret_section_str

            if units == "m":
                scalef = 0.001
            elif units == "mm":
                scalef = 1.0
            else:
                raise ValueError(f'Unknown units "{units}"')
            sec, tap = interpret_section_str(from_str, scalef, units=units)
            self.__dict__.update(sec.__dict__)
        elif outer_poly:
            self._type = "poly"

        self._genprops = GeneralProperties() if genprops is None else genprops
        self._genprops.edit(parent=self)

    def __eq__(self, other):
        for key, val in self.__dict__.items():
            if "parent" in key or "_ifc" in key or key in ["_sec_id", "_guid"]:
                continue
            oval = other.__dict__[key]
            if oval != val:
                return False

        return True

    def _generate_ifc_section_data(self):
        from ada.ifc.utils import create_ifcindexpolyline, create_ifcpolyline

        a = self.parent.parent.get_assembly()
        f = a.ifc_file

        sec_props = dict(ProfileType="AREA", ProfileName=self.name)

        if SectionCat.is_i_profile(self.type):
            if Settings.use_param_profiles is False:
                outer_curve, inner_curve, disconnected = self.cross_sec(True)
                polyline = create_ifcpolyline(f, outer_curve)

                ifc_sec_type = "IfcArbitraryClosedProfileDef"
                sec_props.update(dict(OuterCurve=polyline))
            else:
                if SectionCat.is_strong_axis_symmetric(self) is False:
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
                        OverallWidth=self.w_top,
                        OverallDepth=self.h,
                        WebThickness=self.t_w,
                        FlangeThickness=self.t_ftop,
                    )
                )

        elif SectionCat.is_hp_profile(self.type):
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            points = [f.createIfcCartesianPoint(p) for p in outer_curve]
            ifc_polyline = f.createIfcPolyLine(points)
            ifc_sec_type = "IfcArbitraryClosedProfileDef"
            sec_props.update(dict(OuterCurve=ifc_polyline))

            if Settings.use_param_profiles is True:
                logging.debug(f'Export of "{self.type}" profile to parametric IFC profile is not yet added')

        elif SectionCat.is_box_profile(self.type):
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            outer_points = [f.createIfcCartesianPoint(p) for p in outer_curve + [outer_curve[0]]]
            inner_points = [f.createIfcCartesianPoint(p) for p in inner_curve + [inner_curve[0]]]
            inner_curve = f.createIfcPolyLine(inner_points)
            outer_curve = f.createIfcPolyLine(outer_points)
            ifc_sec_type = "IfcArbitraryProfileDefWithVoids"
            sec_props.update(dict(OuterCurve=outer_curve, InnerCurves=[inner_curve]))

            if Settings.use_param_profiles is True:
                logging.debug(f'Export of "{self.type}" profile to parametric IFC profile is not yet added')

        elif self.type in SectionCat.circular:
            ifc_sec_type = "IfcCircleProfileDef"
            sec_props.update(dict(Radius=self.r))
        elif self.type in SectionCat.tubular:
            ifc_sec_type = "IfcCircleHollowProfileDef"
            sec_props.update(dict(Radius=self.r, WallThickness=self.wt))
        elif self.type in SectionCat.general:
            logging.error("Note! Creating a Circle profile from general section (just for visual inspection as of now)")
            r = np.sqrt(self.properties.Ax / np.pi)
            ifc_sec_type = "IfcCircleProfileDef"
            sec_props.update(dict(Radius=r))
        elif self.type in SectionCat.flatbar:
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            polyline = create_ifcpolyline(f, outer_curve)
            ifc_sec_type = "IfcArbitraryClosedProfileDef"
            sec_props.update(dict(OuterCurve=polyline))

            if Settings.use_param_profiles is True:
                logging.debug(f'Export of "{self.type}" profile to parametric IFC profile is not yet added')

        elif self.type in SectionCat.channels:
            if Settings.use_param_profiles is False:
                outer_curve, inner_curve, disconnected = self.cross_sec(True)
                polyline = create_ifcpolyline(f, outer_curve)
                ifc_sec_type = "IfcArbitraryClosedProfileDef"
                sec_props.update(dict(OuterCurve=polyline))
            else:
                ifc_sec_type = "IfcUShapeProfileDef"
                sec_props.update(
                    dict(Depth=self.h, FlangeWidth=self.w_top, WebThickness=self.t_w, FlangeThickness=self.t_ftop)
                )
        elif self.type == "poly":
            opoly = self.poly_outer
            opoints = [(float(n[0]), float(n[1]), float(n[2])) for n in opoly.seg_global_points]
            opolyline = create_ifcindexpolyline(f, opoints, opoly.seg_index)
            if self.poly_inner is None:
                ifc_sec_type = "IfcArbitraryClosedProfileDef"
                sec_props.update(dict(OuterCurve=opolyline))
            else:
                ipoly = self.poly_inner
                ipoints = [(float(n[0]), float(n[1]), float(n[2])) for n in ipoly.seg_global_points]
                ipolyline = create_ifcindexpolyline(f, ipoints, ipoly.seg_index)
                ifc_sec_type = "IfcArbitraryProfileDefWithVoids"
                sec_props.update(dict(OuterCurve=opolyline, InnerCurves=[ipolyline]))
        else:
            raise ValueError(f'Have yet to implement section type "{self.type}"')

        if self.name is None:
            raise ValueError("Name cannot be None!")

        profile = f.create_entity(ifc_sec_type, **sec_props)

        beamtype = f.createIfcBeamType(
            create_guid(),
            a.user.to_ifc(),
            self.name,
            self.sec_str,
            None,
            None,
            None,
            None,
            None,
            "BEAM",
        )
        return profile, beamtype

    def _import_from_ifc_profile(self, ifc_elem):
        from ada.sections.utils import interpret_section_str

        self._ifc_profile = ifc_elem
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

    @property
    def type(self):
        return self._type

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        if type(value) is not int:
            raise ValueError
        self._id = value

    @property
    def h(self):
        return self._h

    @property
    def w_top(self):
        return self._w_top

    @w_top.setter
    def w_top(self, value):
        """Width of top flange"""
        self._w_top = value

    @property
    def w_btn(self):
        """Width of bottom flange"""
        return self._w_btn

    @w_btn.setter
    def w_btn(self, value):
        self._w_btn = value

    @property
    def t_w(self):
        """Thickness of web"""
        return self._t_w

    @property
    def t_ftop(self):
        """Thickness of top flange"""
        return self._t_ftop

    @property
    def t_fbtn(self):
        """Thickness of bottom flange"""
        return self._t_fbtn

    @property
    def r(self) -> float:
        """Radius (Outer)"""
        return self._r

    @property
    def wt(self) -> float:
        """Wall thickness"""
        return self._wt

    @property
    def sec_str(self):
        def s(x):
            return x / 0.001

        if self.type in SectionCat.box + SectionCat.igirders + SectionCat.tprofiles + SectionCat.shs + SectionCat.rhs:
            sec_str = "{}{:g}x{:g}x{:g}x{:g}".format(self.type, s(self.h), s(self.w_top), s(self.t_w), s(self.t_ftop))
        elif self.type in SectionCat.tubular:
            sec_str = "{}{:g}x{:g}".format(self.type, s(self.r), s(self.wt))
        elif self.type in SectionCat.circular:
            sec_str = "{}{:g}".format(self.type, s(self.r))
        elif self.type in SectionCat.angular:
            sec_str = "{}{:g}x{:g}".format(self.type, s(self.h), s(self.t_w))
        elif self.type in SectionCat.iprofiles:
            sec_str = self._sec_str
        elif self.type in SectionCat.channels:
            sec_str = "{}{:g}".format(self.type, s(self.h))
        elif self.type in SectionCat.general:
            sec_str = "{}{}".format(self.type, self.id)
        elif self.type in SectionCat.flatbar:
            sec_str = f"{self.type}{s(self.h)}x{s(self.w_top)}"
        elif self.type == "poly":
            sec_str = "PolyCurve"
        else:
            raise ValueError(f'Section type "{self.type}" has not been given a section str')
        return sec_str.replace(".", "_") if sec_str is not None else None

    @property
    def properties(self) -> GeneralProperties:
        return self._genprops

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if self._units != value:
            from ada.core.utils import unit_length_conversion

            scale_factor = unit_length_conversion(self._units, value)

            if self.poly_inner is not None:
                self.poly_inner.scale(scale_factor, Settings.point_tol)

            if self.poly_outer is not None:
                self.poly_outer.scale(scale_factor, Settings.point_tol)

            vals = ["h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt"]

            for key in self.__dict__.keys():
                if self.__dict__[key] is not None:
                    if key[1:] in vals:
                        self.__dict__[key] *= scale_factor
            self._units = value

    @property
    def ifc_profile(self):
        if self._ifc_profile is None:
            self._ifc_profile, self._ifc_beam_type = self._generate_ifc_section_data()
        return self._ifc_profile

    @property
    def ifc_beam_type(self):
        if self._ifc_beam_type is None:
            self._ifc_profile, self._ifc_beam_type = self._generate_ifc_section_data()
        return self._ifc_beam_type

    @property
    def poly_outer(self) -> CurvePoly:
        return self._outer_poly

    @property
    def poly_inner(self) -> CurvePoly:
        return self._inner_poly

    def cross_sec(self, is_solid=True):
        """

        :param is_solid: Solid Representation
        :return:
        """
        from .utils import get_profile_props

        return get_profile_props(self, is_solid)

    def cross_sec_shape(
        self,
        solid_repre=True,
        origin=(0.0, 0.0, 0.0),
        xdir=(1.0, 0.0, 0.0),
        normal=(0.0, 0.0, 1.0),
    ):
        """

        :param solid_repre: Solid Representation
        :param origin:
        :param xdir:
        :param normal:
        :return:
        """
        from OCC.Extend.ShapeFactory import make_face, make_wire

        from ada.core.utils import local_2_global_nodes
        from ada.occ.utils import make_circle, make_face_w_cutout, make_wire_from_points

        def points2wire(curve):
            poly = CurvePoly(points2d=curve, origin=origin, xdir=xdir, normal=normal, parent=self)
            return poly.wire

        if self.type in SectionCat.tubular:
            outer_shape = make_wire([make_circle(origin, normal, self.r)])
            inner_shape = make_wire([make_circle(origin, normal, self.r - self.wt)])
        elif self.type in SectionCat.circular:
            outer_shape = make_wire([make_circle(origin, normal, self.r)])
            inner_shape = None
        else:
            outer_curve, inner_curve, disconnected = self.cross_sec(solid_repre)
            if type(outer_curve) is CurvePoly:
                assert isinstance(outer_curve, CurvePoly)
                outer_curve.origin = origin
                face = outer_curve.face
                return face
            if inner_curve is not None:
                # inner_shape = wp2.polyline(inner_curve).close().wire().toOCC()
                inner_shape = points2wire(inner_curve)
                # inner_poly = PolyCurve(points2d=inner_curve, origin=origin, xdir=xdir, normal=normal)
            else:
                inner_shape = None

            if disconnected is False:
                # outer_shape = wp.polyline(outer_curve).close().wire().toOCC()
                outer_shape = points2wire(outer_curve)
                # outer_shape = outer_poly.wire
            else:
                # outer_shape = [wp.polyline(wi).close().wire().toOCC() for wi in outer_curve]
                outer_shape = []
                for p1, p2 in outer_curve:
                    gp1 = local_2_global_nodes([p1], origin, xdir, normal)
                    gp2 = local_2_global_nodes([p2], origin, xdir, normal)
                    outer_shape.append(make_wire_from_points(gp1 + gp2))

        if inner_shape is not None and solid_repre is True:
            shape = make_face_w_cutout(make_face(outer_shape), inner_shape)
        else:
            shape = outer_shape

        return shape

    def _repr_html_(self):
        from IPython.display import display
        from ipywidgets import HBox

        from ada.visualize.renderer import SectionRenderer

        sec_render = SectionRenderer()
        fig, html = sec_render.build_display(self)
        display(HBox([fig, html]))

    def __hash__(self):
        return hash(self.guid)

    def __repr__(self):
        if self.type in SectionCat.circular + SectionCat.tubular:
            return f"Section({self.name}, {self.type}, r: {self.r}, wt: {self.wt})"
        elif self.type in SectionCat.general:
            p = self.properties
            return f"Section({self.name}, {self.type}, Ax: {p.Ax}, Ix: {p.Ix}, Iy: {p.Iy}, Iz: {p.Iz}, Iyz: {p.Iyz})"
        else:
            return (
                f"Section({self.name}, {self.type}, h: {self.h}, w_btn: {self.w_btn}, "
                f"w_top: {self.w_top}, t_fbtn: {self.t_fbtn}, t_ftop: {self.t_ftop}, t_w: {self.t_w})"
            )
