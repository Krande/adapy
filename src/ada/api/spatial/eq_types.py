from __future__ import annotations

from enum import Enum


class EquipRepr(str, Enum):
    AS_IS = "AS_IS"
    LINE_LOAD = "LINE_LOAD"
    BEAMS_AND_MASS = "BEAM_MASS"
    ECCENTRIC_MASS = "ECCENTRIC_MASS"
    FOOTPRINT_MASS = "FOOTPRINT_MASS"
    VERTICAL_BEAMS_AND_MASS = "VERTICAL_BEAM_MASS"
