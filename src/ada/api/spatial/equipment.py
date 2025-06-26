from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Iterable

from .part import Part

if TYPE_CHECKING:
    from ada import LoadConceptCase, Point


class EquipRepr(str, Enum):
    AS_IS = "AS_IS"
    LINE_LOAD = "LINE_LOAD"
    BEAMS_AND_MASS = "BEAM_MASS"
    ECCENTRIC_MASS = "ECCENTRIC_MASS"
    FOOTPRINT_MASS = "FOOTPRINT_MASS"
    VERTICAL_BEAMS_AND_MASS = "VERTICAL_BEAM_MASS"


class Equipment(Part):
    def __init__(
        self,
        name: str,
        mass: float,
        cog: Iterable[float] | Point,
        origin: Iterable[float] | Point,
        lx: float,
        ly: float,
        lz: float,
        eq_repr: EquipRepr = EquipRepr.AS_IS,
        load_case_ref: str | LoadConceptCase = None,
        moment_equilibrium: bool = True,
        footprint: list[tuple[float, float]] = None,
    ):
        from ada import Point

        super(Equipment, self).__init__(name=name)
        self.mass = mass
        self.cog = cog
        if not isinstance(origin, Point):
            origin = Point(*origin)
        self.origin = origin
        self.lx = lx
        self.ly = ly
        self.lz = lz
        self.eq_repr = eq_repr
        self.load_case_ref = load_case_ref
        self.moment_equilibrium = moment_equilibrium
        if footprint is None:
            lx_ = lx / 2
            ly_ = ly / 2
            footprint = [(-lx_, -ly_, lx_, ly_)]
        self.footprint = footprint
