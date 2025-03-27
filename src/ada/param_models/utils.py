from typing import Iterator

from ada import Beam
from ada.core.utils import Counter


def beams_along_polyline(polyline: list[tuple], bm: Beam, name_gen: Iterator = None) -> list[Beam]:
    beams = []
    ngen = name_gen if name_gen is not None else Counter(prefix="bm")
    for p1, p2 in zip(polyline[:-1], polyline[1:]):
        beams.append(bm.copy_to(next(ngen), p1, p2))
    return beams
