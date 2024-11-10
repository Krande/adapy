from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import ada

if TYPE_CHECKING:
    from ada import Part


@dataclass
class SATRefs:
    sat_text: str
    sat_map: dict


def create_sat_from_beams(part: Part) -> SATRefs:
    from ada import Beam

    sat_str = ""
    sat_map = dict()
    for bm in part.get_all_physical_objects(by_type=Beam):
        pass

    return SATRefs(sat_str, sat_map)


def make_ints_if_possible(lst: list[float] | ada.Point | ada.Direction) -> list[float | int]:
    return [int(x) if x.is_integer() else x for x in lst]
