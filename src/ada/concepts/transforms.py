from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from pyquaternion import Quaternion


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

    def to_rot_matrix(self):
        my_quaternion = Quaternion(axis=self.vector, degrees=self.angle)
        return my_quaternion.rotation_matrix


@dataclass
class Placement:
    origin: np.ndarray = np.array([0, 0, 0], dtype=float)
    xdir: np.ndarray = None
    ydir: np.ndarray = None
    zdir: np.ndarray = None

    def __post_init__(self):
        from ada.core.utils import calc_yvec

        all_dir = [self.xdir, self.ydir, self.zdir]
        if all(x is None for x in all_dir):
            self.xdir = np.array([1, 0, 0], dtype=float)
            self.ydir = np.array([0, 1, 0], dtype=float)
            self.zdir = np.array([0, 0, 1], dtype=float)

        if self.ydir is None and all(x is not None for x in [self.xdir, self.zdir]):
            self.ydir = calc_yvec(self.xdir, self.zdir)

        all_dir = [self.xdir, self.ydir, self.zdir]

        if all(x is None for x in all_dir):
            raise ValueError("Placement orientation needs all 3 vectors")

        self.xdir = np.array(self.xdir)
        self.ydir = np.array(self.ydir)
        self.zdir = np.array(self.zdir)
