from __future__ import annotations
from enum import Enum
from typing import TYPE_CHECKING

from ada.geom.placement import Direction

if TYPE_CHECKING:
    from ada import Beam


class Justification(Enum):
    NA = "neutral axis"
    TOS = "top of steel"
    CUSTOM = "custom"


def get_offset_from_justification(beam: Beam, just: Justification) -> Direction:
    if just == Justification.NA:
        return Direction(0, 0, 0)
    elif just == Justification.TOS:
        return beam.up * beam.section.h / 2
    elif just == Justification.CUSTOM:
        pass
    else:
        raise ValueError(f"Unknown justification: {just}")
