from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Iterable

from .part import Part

if TYPE_CHECKING:
    from ada import LoadConceptCase, Point


class EquipRepr(str, Enum):
    AS_IS = "as_is"
    LINE_LOAD = "line_load"
    BEAMS_AND_MASS = "beams_mass"
    ECCENTRIC_MASS = "ecc_mass"
    FOOTPRINT_MASS = "foot_mass"
    VERTICAL_BEAMS_AND_MASS = "vertical_beams_and_mass"


class Equipment(Part):
    def __init__(
        self,
        name: str,
        mass_dry: float,
        mass_content: float,
        cog: Iterable[float] | Point,
        origin: Iterable[float] | Point,
        lx: float,
        ly: float,
        lz: float,
        eq_repr: EquipRepr = EquipRepr.AS_IS,
        load_case_ref: str | LoadConceptCase = None,
    ):
        super(Equipment, self).__init__(name=name)
        self.mass_dry = mass_dry
        self.mass_content = mass_content
        self.cog = cog
        self.origin = origin
        self.lx = lx
        self.ly = ly
        self.lz = lz
        self.eq_repr = eq_repr
        self.load_case_ref = load_case_ref
