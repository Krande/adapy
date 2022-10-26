from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Beam


def ec3_654(bm: Beam, forces: list[float]):
    if len(forces) != 6:
        raise ValueError("Length of Forces must be 6")

    _ = bm.section.properties
