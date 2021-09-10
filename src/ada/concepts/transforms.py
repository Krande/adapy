from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class Transform:
    translation: Iterable[float, float, float] = None
    rotation: Rotation = None

    def to_ifc(self, f):
        from ada.ifc.utils import export_transform

        return export_transform(f, self)


@dataclass
class Rotation:
    origin: Iterable[float, float, float]
    vector: Iterable[float, float, float]
    angle: float
