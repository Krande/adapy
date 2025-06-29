from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Iterable

from ada.api.beams import geom_beams as geo_conv
from ada.config import logger
from ada.geom import Geometry
from ada.geom.curves import IndexedPolyCurve
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.geom.surfaces import ArbitraryProfileDef
from ada.sections.string_to_section import interpret_section_str

from .base_bm import Beam

if TYPE_CHECKING:
    from ada import Section


class TaperTypes(Enum):
    FLUSH_TOP = "flush"
    CENTERED = "centered"
    FLUSH_BOTTOM = "flush_bottom"

    @classmethod
    def from_str(cls, value: str):
        for item in cls:
            if item.value.lower() == value.lower():
                return item
        raise ValueError(f"{value} is not a valid taper type")


class BeamTapered(Beam):
    def __init__(
        self,
        name,
        n1: Iterable,
        n2: Iterable,
        sec: str | Section,
        tap: str | Section = None,
        taper_type: TaperTypes | str = TaperTypes.CENTERED,
        **kwargs,
    ):
        super().__init__(name=name, n1=n1, n2=n2, sec=sec, **kwargs)

        if isinstance(sec, str) and tap is None:
            sec, tap = interpret_section_str(sec)

        if isinstance(tap, str):
            tap, _ = interpret_section_str(tap)

        self._taper = tap
        self._taper.refs.append(self)
        self._taper.parent = self
        if isinstance(taper_type, str):
            taper_type = TaperTypes.from_str(taper_type)
        self._taper_type = taper_type

    @property
    def taper(self) -> Section:
        return self._taper

    @taper.setter
    def taper(self, value: Section):
        self._taper = value

    @property
    def taper_type(self) -> TaperTypes:
        return self._taper_type

    @taper_type.setter
    def taper_type(self, value: TaperTypes):
        self._taper_type = value

    def solid_geom(self) -> Geometry:
        geo = geo_conv.straight_tapered_beam_to_geom(self)
        if self.taper_type == TaperTypes.CENTERED:
            return geo

        if self.up.is_equal(Direction(0, 0, 1)):
            off_dir = -1
        elif self.up.is_equal(Direction(0, 0, -1)):
            off_dir = 1
        else:
            logger.warning("Tapered beam is not aligned with global z-axis")
            off_dir = 0

        if self.taper_type == TaperTypes.FLUSH_TOP:
            offset_dir_1 = Direction(0, off_dir * self.section.h / 2)
            offset_dir_2 = Direction(0, off_dir * self.taper.h / 2)
        elif self.taper_type == TaperTypes.FLUSH_BOTTOM:
            offset_dir_1 = Direction(0, -off_dir * self.section.h / 2)
            offset_dir_2 = Direction(0, -off_dir * self.taper.h / 2)
        else:
            raise ValueError(f"Unknown taper type {self.taper_type}")

        profile_1 = geo.geometry.swept_area

        if isinstance(profile_1, ArbitraryProfileDef):
            if isinstance(profile_1.outer_curve, IndexedPolyCurve):
                for curve in profile_1.outer_curve.segments:
                    curve.start = Point(curve.start + offset_dir_1)
                    curve.end = Point(curve.end + offset_dir_1)

        profile_2 = geo.geometry.end_swept_area

        if isinstance(profile_2, ArbitraryProfileDef):
            if isinstance(profile_2.outer_curve, IndexedPolyCurve):
                for curve in profile_2.outer_curve.segments:
                    curve.start = Point(curve.start + offset_dir_2)
                    curve.end = Point(curve.end + offset_dir_2)

        return geo

    def shell_geom(self) -> Geometry:
        geom = geo_conv.straight_tapered_beam_to_geom(self, is_solid=False)
        return geom

    def __repr__(self):
        p1s = self.n1.p.tolist()
        p2s = self.n2.p.tolist()
        secn = self.section.sec_str
        tapn = self.taper.sec_str
        matn = self.material.name
        return f'{self.__class__.__name__}("{self.name}", {p1s}, {p2s}, "{secn}","{tapn}", "{matn}")'
