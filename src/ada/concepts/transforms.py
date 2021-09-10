from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class Transform:
    translation: Tuple[float, float, float]
    rotation: Rotation

    def to_ifc(self, f):
        from ada.ifc.utils import export_transform

        return export_transform(f, self)


@dataclass
class Rotation:
    origin: Tuple[float, float, float]
    vector: Tuple[float, float, float]
    angle: float
