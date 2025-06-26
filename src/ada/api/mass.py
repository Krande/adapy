from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ada.api.primitives.sphere import PrimSphere

if TYPE_CHECKING:
    from ada import Placement, Point
    from ada.api.nodes import numeric


class MassPoint(PrimSphere):
    """Concept mass point object, added to handle export to genie xml without needing to use fem-object"""

    def __init__(
        self,
        name: str,
        p: Point | Iterable[numeric, numeric, numeric],
        mass: float,
        radius=0.2,
        placement: Placement = None,
    ):
        super().__init__(name=name, cog=p, radius=radius, placement=placement)
        self.mass = mass

    def __repr__(self):
        return f"{self.__class__.__name__}([{self.cog}, {self.mass})"
