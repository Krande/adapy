from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ada.base.root import Root
from ada.base.units import Units
from ada.config import Config, get_logger
from ada.sections.categories import BaseTypes, SectionCat

if TYPE_CHECKING:
    from ada import Beam, BeamTapered, CurvePoly2d, Pipe, PipeSegElbow, PipeSegStraight
    from ada.fem import FemSection
    from ada.sections.profiles import SectionProfile

logger = get_logger()


class Section(Root):
    TYPES = BaseTypes

    def __init__(
        self,
        name,
        sec_type: BaseTypes | str = None,
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
        outer_poly: CurvePoly2d = None,
        inner_poly: CurvePoly2d = None,
        genprops: GeneralProperties = None,
        metadata=None,
        units=Units.M,
        guid=None,
        refs=None,
        auto_display_in_html=True,
    ):
        super(Section, self).__init__(name=name, guid=guid, metadata=metadata, units=units, parent=parent)
        if isinstance(sec_type, str):
            sec_type_in = sec_type
            sec_type = BaseTypes.from_str(sec_type_in)
            if sec_type is None:
                raise ValueError(f"Unrecognized section type {sec_type_in}]")

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

        self._ifc_profile = None
        self._ifc_beam_type = None

        if from_str is not None:
            from ada.sections.string_to_section import interpret_section_str

            if units == Units.M:
                scalef = 0.001
            elif units == Units.MM:
                scalef = 1.0
            else:
                raise ValueError(f'Unknown units "{units}"')

            sec, tap = interpret_section_str(from_str, scalef, units=units)
            self.__dict__.update(sec.__dict__)

        self._genprops = None
        self._refs = refs if refs is not None else []
        if genprops is not None:
            genprops.parent = self
            self._genprops = genprops

        self.auto_display_in_html = auto_display_in_html

    def equal_props(self, other: Section):
        for propa, propb in zip(self.unique_props(), other.unique_props()):
            if propa != propb:
                return False

        return True

    def unique_props(self):
        props = ["type", "h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt", "poly_outer", "poly_inner"]
        if self.type == self.TYPES.GENERAL:
            props += ["properties"]
        return tuple([getattr(self, p) for p in props])

    @staticmethod
    def from_str(section_str: str) -> Section | list[Section]:
        """Create a section from a string representation. If tapered, returns a list of two sections"""
        from ada.sections.string_to_section import interpret_section_str

        sec, tap = interpret_section_str(section_str)
        if sec != tap and tap is not None:
            return [sec, tap]

        return sec

    @property
    def type(self) -> BaseTypes:
        return self._type

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        if not isinstance(value, int):
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

    @r.setter
    def r(self, value: float):
        self._r = value
        self._genprops = None

    @property
    def wt(self) -> float:
        """Wall thickness"""
        return self._wt

    @wt.setter
    def wt(self, value: float):
        self._wt = value
        self._genprops = None

    @property
    def sec_str(self):
        def s(x):
            return x / 0.001

        if self.type == BaseTypes.BOX:
            sec_str = "{}{:g}x{:g}x{:g}x{:g}".format(
                self.type.value, s(self.h), s(self.w_top), s(self.t_w), s(self.t_ftop)
            )
        elif self.type == BaseTypes.TUBULAR:
            sec_str = "{}{:g}x{:g}".format(self.type.value, s(self.r), s(self.wt))
        elif self.type == BaseTypes.CIRCULAR:
            sec_str = "{}{:g}".format(self.type.value, s(self.r))
        elif self.type == BaseTypes.ANGULAR:
            sec_str = "{}{:g}x{:g}".format(self.type.value, s(self.h), s(self.t_w))
        elif self.type == BaseTypes.IPROFILE:
            sec_str = self._sec_str
        elif self.type == BaseTypes.TPROFILE:
            sec_str = "{}{:g}x{:g}x{:g}".format(self.type.value, s(self.h), s(self.w_top), s(self.t_w))
        elif self.type == BaseTypes.CHANNEL:
            sec_str = "{}{:g}".format(self.type.value, s(self.h))
        elif self.type == BaseTypes.GENERAL:
            sec_str = "{}{}".format(self.type.value, self.id)
        elif self.type == BaseTypes.FLATBAR:
            sec_str = f"{self.type.value}{s(self.h)}x{s(self.w_top)}"
        elif self.type == BaseTypes.POLY:
            sec_str = "PolyCurve"

        else:
            raise ValueError(f'Section type "{self.type}" has not been given a section str')

        return sec_str.replace(".", "_") if sec_str is not None else None

    @property
    def properties(self) -> GeneralProperties:
        if self._genprops is None:
            from .properties import calculate_general_properties

            self._genprops = calculate_general_properties(self)

        return self._genprops

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if self._units != value:
            scale_factor = Units.get_scale_factor(self._units, value)

            if self.poly_inner is not None:
                self.poly_inner.scale(scale_factor, Config().general_point_tol)

            if self.poly_outer is not None:
                self.poly_outer.scale(scale_factor, Config().general_point_tol)

            vals = ["h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt"]

            for key in self.__dict__.keys():
                if self.__dict__[key] is not None:
                    if key[1:] in vals:
                        self.__dict__[key] *= scale_factor
            self._units = value

    @property
    def poly_outer(self) -> CurvePoly2d:
        return self._outer_poly

    @property
    def poly_inner(self) -> CurvePoly2d:
        return self._inner_poly

    def get_section_profile(self, is_solid=True) -> SectionProfile:
        from .profiles import build_section_profile

        return build_section_profile(self, is_solid)

    def show(self, return_as_html_str=False):
        from IPython.display import HTML, display

        from ada.visit.plots import section_overview_to_html_str

        if return_as_html_str:
            return section_overview_to_html_str(self)

        display(HTML(section_overview_to_html_str(self)))

    def _repr_html_(self):
        if self.auto_display_in_html:
            self.show()

    @property
    def refs(self) -> list[Beam | BeamTapered | FemSection | Pipe | PipeSegStraight | PipeSegElbow]:
        return self._refs

    def __hash__(self):
        return hash(self.guid)

    def __eq__(self, other: Section):
        if not isinstance(other, Section):
            return False
        return self._guid == other._guid

    def __repr__(self):
        if self.type in SectionCat.circular + SectionCat.tubular:
            props_str = f"r: {self.r}, wt: {self.wt}"
        elif self.type in SectionCat.general:
            p = self.properties
            props_str = f"Ax: {p.Ax}, Ix: {p.Ix}, Iy: {p.Iy}, Iz: {p.Iz}, Iyz: {p.Iyz}"
        else:
            props_str = (
                f"h: {self.h}, w_btn: {self.w_btn}, w_top: {self.w_top}, t_fbtn: {self.t_fbtn}, "
                f"t_ftop: {self.t_ftop}, t_w: {self.t_w}"
            )
        return f"{self.__class__.__name__}({self.name}, {self.type}, {props_str})"

    def copy_to(self, name: str = None):
        """Make a copy of the section"""
        return self.__class__(
            name=name if name is not None else self.name,
            sec_type=self.type,
            h=self.h,
            w_top=self.w_top,
            w_btn=self.w_btn,
            t_w=self.t_w,
            t_ftop=self.t_ftop,
            t_fbtn=self.t_fbtn,
            r=self.r,
            wt=self.wt,
            sec_str=self.sec_str,
            outer_poly=self.poly_outer.copy_to() if self.poly_outer else None,
            inner_poly=self.poly_inner.copy_to() if self.poly_inner else None,
        )


class SectionI(Section):
    def __init__(self, name: str, h, w_top, t_w, t_ftop, w_btn=None, t_fbtn=None, **kwargs):

        super().__init__(name=name, sec_type=BaseTypes.IPROFILE)
        self._h = h
        self._w_top = w_top
        self._w_btn = w_btn
        self._t_w = t_w
        self._t_ftop = t_ftop
        self._t_fbtn = t_fbtn
        self._sec_str = None


class SectionBox(Section):
    def __init__(self, name: str, h, w_top, t_w, t_ftop, w_btn=None, t_fbtn=None, **kwargs):
        super().__init__(name=name, sec_type=BaseTypes.BOX)
        self._h = h
        self._w_top = w_top
        self._w_btn = w_btn
        self._t_w = t_w
        self._t_ftop = t_ftop
        self._t_fbtn = t_fbtn
        self._sec_str = None


class SectionTubular(Section): ...


class SectionCircular(Section): ...


class SectionT(Section): ...


class SectionChannel(Section): ...


class SectionAngle(Section): ...


class SectionFlatBar(Section): ...


class SectionPoly(Section): ...


class SectionGeneral(Section): ...


class SectionParts:
    WEB = "web"
    TOP_FLANGE = "top_fl"
    BTN_FLANGE = "btn_fl"


@dataclass
class GeneralProperties:
    parent: Section = field(default=None, compare=False, repr=False)
    Ax: float = None
    Ix: float = None
    Iy: float = None
    Iz: float = None
    Iyz: float = None
    Wxmin: float = None
    Wymin: float = None
    Wzmin: float = None
    Shary: float = None
    Sharz: float = None
    Shceny: float = None
    Shcenz: float = None
    Sy: float = None
    Sz: float = None
    Sfy: float = 1
    Sfz: float = 1
    Cy: float = None
    Cz: float = None

    def __hash__(self):
        return hash(
            (
                self.Ax,
                self.Ix,
                self.Iy,
                self.Iz,
                self.Iyz,
                self.Wxmin,
                self.Wymin,
                self.Wzmin,
                self.Shary,
                self.Sharz,
                self.Shceny,
                self.Shcenz,
                self.Sy,
                self.Sz,
                self.Sfy,
                self.Sfz,
                self.Cy,
                self.Cz,
            )
        )

    @property
    def modified(self) -> bool:
        """Returns true if attributes are not equal to the calculated properties of the parent section"""
        return self != self.calc_parent_properties()

    def calc_parent_properties(self) -> GeneralProperties:
        """Returns calculated properties based on parent section"""
        from ada.sections.properties import calculate_general_properties

        return calculate_general_properties(self.parent)
