from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, List, Union

import numpy as np
import pyquaternion as pq

from ada.core.vector_utils import transform, transform_csys_to_csys, transform_2d_to_3d, calc_xvec
from ada.geom.placement import Direction, O, Axis2Placement3D
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada import Part
    from ada.base.physical_objects import BackendGeom


@dataclass
class Transform:
    translation: np.ndarray = None
    rotation: Rotation = None


@dataclass
class Rotation:
    origin: Iterable[float, float, float]
    vector: Iterable[float, float, float]
    angle: float

    def to_rot_matrix(self):
        my_quaternion = pq.Quaternion(axis=self.vector, degrees=self.angle)
        return my_quaternion.rotation_matrix

    def rotate_point(self, p: Union[tuple, list]):
        p1 = np.array(self.origin)
        rot_mat = self.to_rot_matrix()
        p_norm = np.array(p) - p1
        res = p1 + p_norm @ rot_mat.T
        return res


@dataclass
class Placement:
    origin: Iterable | Point = field(default_factory=O)
    xdir: Iterable | Direction = None
    ydir: Iterable | Direction = None
    zdir: Iterable | Direction = None
    scale: float = 1.0
    parent = None

    def __post_init__(self):
        from ada.core.vector_utils import calc_yvec, calc_xvec, calc_zvec

        all_dir = [self.xdir, self.ydir, self.zdir]
        if all(x is None for x in all_dir):
            self.xdir = Direction(1, 0, 0)
            self.ydir = Direction(0, 1, 0)
            self.zdir = Direction(0, 0, 1)

        if self.ydir is None and all(x is not None for x in [self.xdir, self.zdir]):
            self.ydir = calc_yvec(self.xdir, self.zdir)

        if self.xdir is None and all(x is not None for x in [self.ydir, self.zdir]):
            self.xdir = calc_xvec(self.ydir, self.zdir)

        if self.zdir is None and all(x is not None for x in [self.xdir, self.ydir]):
            self.zdir = calc_zvec(self.xdir, self.ydir)

        all_dir = [self.xdir, self.ydir, self.zdir]
        all_vec = ['xdir', "ydir", "zdir"]
        vectors = [n for n, x in zip(all_vec, all_dir) if x is not None]
        if len(vectors) == 1:
            raise ValueError(f"Placement only given '{vectors[0]}' vector. "
                             "Please supply at least two vectors to define a placement.")

        self.xdir = Point(*self.xdir)
        self.ydir = Point(*self.ydir)
        self.zdir = Point(*self.zdir)
        if self.origin is None:
            self.origin = Point(0, 0, 0)

        if not isinstance(self.origin, Point):
            self.origin = Point(*self.origin)


    @staticmethod
    def from_axis_angle(axis: list[float], angle: float, origin: Iterable[float | int] = None) -> Placement:
        """Axis is a list of 3 floats, angle is in degrees."""
        q = pq.Quaternion(axis=axis, angle=np.radians(angle))
        m = q.transformation_matrix

        return Placement(origin=origin, xdir=m[0, :3], ydir=m[1, :3], zdir=m[2, :3])

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

        return make_ori_vector("VecGeom", self.origin, self.rot_matrix, **kwargs)

    @property
    def rot_matrix(self):
        return np.array([self.xdir, self.ydir, self.zdir])

    def calc_matrix4x4(self):
        # Based on the quaternion transformation matrix calculation
        t = np.array([[self.origin[0]], [self.origin[1]], [self.origin[2]]])
        Rt = np.hstack([self.rot_matrix, t])
        return np.vstack([Rt, np.array([0.0, 0.0, 0.0, 1.0])])

    def to_axis3d_geom(self) -> Axis2Placement3D:
        return Axis2Placement3D(Point(*self.origin), Direction(*self.zdir), Direction(*self.xdir))

    def transform_points_to_global(self, points2d: Iterable[Iterable[float | int, float | int]]) -> np.ndarray:
        """Transform points from the coordinate system of this placement to the global coordinate system."""
        if not isinstance(points2d, np.ndarray):
            points2d = np.array(points2d)

        m = self.calc_matrix4x4()
        res2 = transform_2d_to_3d(points2d, m)

        return res2

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

        from ada.cadit.ifc.utils import create_guid

        matrices = [[self.instance_ref.guid, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]]
        for place in self.placements:
            q1 = Quaternion(matrix=np.array(place.rot_matrix))
            rmat = q1.rotation_matrix
            matrices.append(
                [create_guid(), *place.origin.astype(float).tolist(), *np.concatenate(rmat).astype(float).tolist()]
            )

        return matrices
