from __future__ import annotations

from typing import TYPE_CHECKING

from ada.api.beams import geom_beams as geo_conv
from ada.api.curves import CurveOpen2d
from ada.geom import Geometry

from .base_bm import Beam

if TYPE_CHECKING:
    from ada import Section


class BeamSweep(Beam):
    def __init__(self, name: str, curve: CurveOpen2d, sec: str | Section, **kwargs):
        n1 = curve.points3d[0]
        n2 = curve.points3d[-1]
        super().__init__(name=name, n1=n1, n2=n2, sec=sec, **kwargs)
        self._curve = curve
        curve.parent = self

    @property
    def curve(self) -> CurveOpen2d:
        return self._curve

    def solid_geom(self) -> Geometry:
        return geo_conv.swept_beam_to_geom(self)
