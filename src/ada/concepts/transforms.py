from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, List, Union

import numpy as np
from pyquaternion import Quaternion

if TYPE_CHECKING:
    from ada import Part
    from ada.base.physical_objects import BackendGeom


@dataclass
class Transform:
    translation: np.ndarray = None
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

    def rotate_point(self, p: Union[tuple, list]):
        p1 = np.array(self.origin)
        rot_mat = self.to_rot_matrix()
        p_norm = np.array(p) - p1
        res = p1 + p_norm @ rot_mat.T
        return res


@dataclass
class Placement:
    origin: Union[list, tuple, np.ndarray] = (0, 0, 0)
    xdir: Union[list, tuple, np.ndarray] = None
    ydir: Union[list, tuple, np.ndarray] = None
    zdir: Union[list, tuple, np.ndarray] = None
    scale: float = 1.0
    parent = None

    def __post_init__(self):
        from ada.core.vector_utils import calc_yvec

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

        self.xdir = np.array(self.xdir, dtype=float)
        self.ydir = np.array(self.ydir, dtype=float)
        self.zdir = np.array(self.zdir, dtype=float)

        if not isinstance(self.origin, np.ndarray):
            self.origin = np.array(self.origin, dtype=float)

    def absolute_placement(self):
        current_location = np.array([0, 0, 0], dtype=float)
        ancestry = self.parent.get_ancestors()
        ancestry.reverse()
        for ancestor in ancestry:
            current_location += ancestor.placement.origin
            # TODO: Add support for combining rotations as well
        return current_location

    def to_vector_geom(self, **kwargs) -> "Part":
        from ada.occ.utils import make_ori_vector

        return make_ori_vector("VecGeom", self.origin, self.csys, **kwargs)

    @property
    def csys(self):
        return [self.xdir, self.ydir, self.zdir]

    def __eq__(self, other: Placement):
        from ada.core.vector_utils import vector_length

        for prop in ["origin", "xdir", "ydir", "zdir"]:
            if vector_length(getattr(other, prop) - getattr(self, prop)) > 0.0:
                return False

        return True


@dataclass
class Instance:
    instance_ref: Union["Part", "BackendGeom"]
    placements: List[Placement] = field(default_factory=list)

    def to_list_of_custom_json_matrices(self):
        from pyquaternion import Quaternion

        from ada.ifc.utils import create_guid

        matrices = [[self.instance_ref.guid, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]]
        for place in self.placements:
            q1 = Quaternion(matrix=np.array(place.csys))
            rmat = q1.rotation_matrix
            matrices.append(
                [create_guid(), *place.origin.astype(float).tolist(), *np.concatenate(rmat).astype(float).tolist()]
            )

        return matrices
