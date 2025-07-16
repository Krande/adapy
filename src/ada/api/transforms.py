from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Iterable, List, Union

import numpy as np
import pyquaternion as pq

from ada.core.vector_transforms import (
    compute_orientation_vec,
    normal_to_points_in_plane,
    transform_3x3,
)
from ada.core.vector_utils import calc_xvec, calc_yvec, unit_vector
from ada.geom.direction import Direction
from ada.geom.placement import XV, YV, ZV, Axis2Placement3D, O
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

    _is_identity: bool = field(default=None, init=False)

    def __post_init__(self):
        from ada.geom.placement import O

        # Convert input directions to tuples for caching
        xdir = tuple(self.xdir) if self.xdir is not None else None
        ydir = tuple(self.ydir) if self.ydir is not None else None
        zdir = tuple(self.zdir) if self.zdir is not None else None

        # Use cached compute_orientation_vec function
        xv, yv, zv = compute_orientation_vec(xdir, ydir, zdir)

        self.xdir = Direction(xv)
        self.ydir = Direction(yv)
        self.zdir = Direction(zv)

        # Set origin
        if self.origin is None:
            self.origin = O()
        elif not isinstance(self.origin, Point):
            # Check if origin is a common point
            if hasattr(self.origin, "__iter__") and len(self.origin) == 3:
                if tuple(self.origin) == (0.0, 0.0, 0.0):
                    self.origin = O()
                else:
                    self.origin = Point(*self.origin)
            else:
                self.origin = Point(*self.origin)

    def __getitem__(self, key):
        return [self.xdir, self.ydir, self.zdir][key]

    @staticmethod
    def from_quaternion(quat: pq.Quaternion, origin=None):
        rot_mat = quat.rotation_matrix
        return Placement(origin=origin, xdir=rot_mat[0], ydir=rot_mat[1], zdir=rot_mat[2])

    @staticmethod
    def from_axis_angle(axis: list[float], angle: float, origin: Iterable[float | int] = None) -> Placement:
        """Axis is a list of 3 floats, angle is in degrees."""
        q = pq.Quaternion(axis=axis, angle=np.radians(angle))
        m = q.transformation_matrix

        return Placement(origin=origin, xdir=m[0, :3], ydir=m[1, :3], zdir=m[2, :3])

    @staticmethod
    def from_co_linear_points(points: list[Point] | np.ndarray, xdir=None, flip_n=False) -> Placement:
        """Create a placement from a list of points that are co-linear."""
        if not isinstance(points, np.ndarray):
            points = np.asarray(points)

        if points.shape[0] < 3:
            raise ValueError("At least three points are required to define a placement.")

        origin = points[0]
        n = normal_to_points_in_plane(points)
        if flip_n:
            n = -n
        if xdir is None:
            xdir = Direction(points[1] - points[0]).get_normalized()
        else:
            xdir = Direction(xdir).get_normalized()

        ydir = calc_yvec(xdir, n)
        return Placement(origin=origin, xdir=xdir, ydir=ydir, zdir=n)

    @staticmethod
    def from_axis3d(axis: Axis2Placement3D) -> Placement:
        return Placement(origin=axis.location, xdir=axis.ref_direction, zdir=axis.axis)

    @staticmethod
    def from_4x4_matrix(matrix: np.ndarray) -> Placement:
        return Placement(
            origin=matrix[:3, 3],
            xdir=matrix[:3, 0],
            ydir=matrix[:3, 1],
            zdir=matrix[:3, 2],
        )

    def get_absolute_placement(self, include_rotations=False) -> Placement:
        if self.parent is None:
            return self

        current_location = self.origin.copy()
        q = pq.Quaternion(matrix=self.rot_matrix)
        ancestry = self.parent.get_ancestors(include_self=False)

        for ancestor in ancestry:
            current_location += ancestor.placement.origin
            q *= pq.Quaternion(matrix=ancestor.placement.rot_matrix)

        if include_rotations:
            m = q.transformation_matrix
            return Placement(origin=current_location, xdir=m[0, :3], ydir=m[1, :3], zdir=m[2, :3])

        return Placement(origin=current_location, xdir=self.xdir, ydir=self.ydir, zdir=self.zdir)

    def rotate(self, axis: Iterable[float], angle: float) -> Placement:
        """Rotate the placement around an axis. Returns a new placement."""

        q0 = pq.Quaternion(matrix=self.rot_matrix)
        q = q0 * pq.Quaternion(axis=axis, angle=np.radians(angle))
        m = q.transformation_matrix

        return Placement(origin=self.origin, xdir=m[0, :3], ydir=m[1, :3], zdir=m[2, :3])

    @property
    def rot_matrix(self):
        return np.array([self.xdir, self.ydir, self.zdir])

    def get_matrix4x4(self):
        # Based on the quaternion transformation matrix calculation
        t = np.array([[self.origin[0]], [self.origin[1]], [self.origin[2]]])
        Rt = np.hstack([self.rot_matrix, t])
        return np.vstack([Rt, np.array([0.0, 0.0, 0.0, 1.0])])

    def transform_vector(self, vec: Iterable[float | int], inverse=False) -> np.ndarray:
        """Transform a vector from the coordinate system of this placement to the global coordinate system."""
        if not isinstance(vec, np.ndarray):
            vec = np.array(vec)

        vec3d = transform_3x3(self.rot_matrix, np.array([vec]), inverse=inverse)
        return vec3d[0]

    def transform_array_from_other_place(
        self, arr: np.ndarray, other_place: Placement, ignore_translation=False
    ) -> np.ndarray:
        """Transform an array of vectors from the coordinate system of this placement to another coordinate system."""
        # Rotation matrix from old placement to new placement
        rotation_mat = self.rot_matrix @ np.linalg.inv(other_place.rot_matrix)

        # Transform the vector
        if ignore_translation:
            transformed_vec = arr @ rotation_mat.T
        else:
            transformed_vec = (arr - other_place.origin) @ rotation_mat.T + self.origin
        return transformed_vec

    def transform_local_points_to_global(
        self, points2d: Iterable[Iterable[float | int, float | int]], inverse=False
    ) -> np.ndarray:
        """Transform points from the coordinate system of this placement to the global coordinate system."""
        if not isinstance(points2d, np.ndarray):
            points2d = np.array(points2d)

        points3d = transform_3x3(self.rot_matrix, np.asarray(points2d))
        points3d += self.origin

        return points3d

    def transform_local_points_back_to_global(self, points2d):
        if not isinstance(points2d, np.ndarray):
            points2d = np.array(points2d)

        points3d = transform_3x3(self.rot_matrix, points2d, inverse=True)
        points3d += self.origin

        return points3d

    def transform_global_points_back_to_local(self, points3d):
        """Transform points from the global coordinate system to the coordinate system of this placement."""
        points3d_ = np.array(points3d) - self.origin
        points2d = transform_3x3(self.rot_matrix, points3d_, inverse=True)
        return points2d[:, :2]

    def transform_global_points_to_local(self, points3d):
        """Transform points from the global coordinate system to the coordinate system of this placement."""
        points3d_ = np.array(points3d) - np.array(self.origin)
        points2d = transform_3x3(self.rot_matrix, points3d_, inverse=False)

        return points2d[:, :2]

    def to_axis2placement3d(self, use_absolute_placement=True) -> Axis2Placement3D:
        if use_absolute_placement:
            abs_place = self.get_absolute_placement()
            return Axis2Placement3D(location=abs_place.origin, axis=abs_place.zdir, ref_direction=abs_place.xdir)

        return Axis2Placement3D(
            location=self.origin,
            axis=Direction(self.zdir).get_normalized(),
            ref_direction=Direction(self.xdir).get_normalized(),
        )

    def is_identity(self, use_absolute_placement=True) -> bool:
        if self._is_identity is None:
            if use_absolute_placement:
                place = self.get_absolute_placement()
            else:
                place = self

            self._is_identity = place == Placement(O(), XV(), YV(), ZV())

        return self._is_identity

    def with_zdir(self, new_zdir: Direction | Iterable[float]) -> Placement:
        """Returns a new Placement with the zdir transformed to match new_zdir."""
        if not isinstance(new_zdir, Direction):
            new_zdir = Direction(new_zdir)
        new_zdir = new_zdir.get_normalized()  # Ensure it's a unit vector
        current_zdir = self.zdir

        # If already aligned, return the same placement
        if np.allclose(current_zdir, new_zdir):
            return self

        # Compute rotation quaternion from current zdir to new zdir
        axis = np.cross(current_zdir, new_zdir)
        angle = np.arccos(np.clip(np.dot(current_zdir, new_zdir), -1.0, 1.0))

        if np.allclose(axis, 0):  # If vectors are opposite, choose an arbitrary perpendicular axis
            axis = np.array([1, 0, 0]) if abs(current_zdir[0]) < 0.9 else np.array([0, 1, 0])

        rotation_quat = pq.Quaternion(axis=axis, radians=angle)

        # Apply rotation to the coordinate system vectors
        new_xdir = rotation_quat.rotate(self.xdir)
        new_ydir = rotation_quat.rotate(self.ydir)

        return Placement(origin=self.origin, xdir=new_xdir, ydir=new_ydir, zdir=new_zdir)

    def __eq__(self, other: Placement):
        from ada.core.vector_utils import vector_length

        for prop in ["origin", "xdir", "ydir", "zdir"]:
            if vector_length(getattr(other, prop) - getattr(self, prop)) > 0.0:
                return False

        return True

    def __ne__(self, other: Placement):
        return not self.__eq__(other)

    def copy_to(self) -> Placement:
        """Make a copy of this placement"""

        return Placement(
            origin=self.origin.copy(),
            xdir=self.xdir.copy(),
            ydir=self.ydir.copy(),
            zdir=self.zdir.copy(),
            scale=self.scale,
        )


@dataclass
class Instance:
    instance_ref: Union["Part", "BackendGeom"]
    placements: List[Placement] = field(default_factory=list)

    def to_list_of_custom_json_matrices(self):
        from pyquaternion import Quaternion

        from ada.core.guid import create_guid

        matrices = [[self.instance_ref.guid, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]]
        for place in self.placements:
            q1 = Quaternion(matrix=np.array(place.rot_matrix))
            rmat = q1.rotation_matrix
            matrices.append(
                [create_guid(), *place.origin.astype(float).tolist(), *np.concatenate(rmat).astype(float).tolist()]
            )

        return matrices


class Plane(Enum):
    XY = "xy"
    XZ = "xz"
    YZ = "yz"


@dataclass
class EquationOfPlane:
    point_in_plane: tuple | list | np.ndarray
    normal: tuple | list | np.ndarray
    yvec: tuple | list | np.ndarray = None

    PLANE: ClassVar[Plane]

    def __post_init__(self):
        point_in_plane = self.point_in_plane
        normal = self.normal
        x1, y1, z1 = point_in_plane
        a = normal[0]
        b = normal[1]
        c = normal[2]
        self.d = -(a * x1 + b * y1 + c * z1)

    @staticmethod
    def from_arbitrary_points(points):
        points = np.array(points)
        normal = normal_to_points_in_plane(points)
        pip = points[0]
        return EquationOfPlane(pip, normal)

    def calc_distance_to_point(self, point: Iterable | Point) -> float:
        if not isinstance(point, Point):
            point = Point(*point)

        return abs(point.dot(self.normal) + self.d) / np.linalg.norm(self.normal)

    def return_points_in_plane(self, points: np.ndarray) -> np.ndarray:
        return points[points.dot(self.normal) + self.d == 0]

    def is_point_in_plane(self, point: Iterable) -> bool:
        if isinstance(point, np.ndarray) is False:
            point = np.array(point)

        return bool(point.dot(self.normal) + self.d == 0)

    def get_lcsys(self):
        if self.yvec is None:
            if sum(abs(self.normal) - np.array([0, 0, 1])) < 1e-5:
                vec1 = np.array([1, 0, 0])
            else:
                vec1 = np.array([0, 0, 1])

            self.yvec = unit_vector(calc_yvec(vec1, self.normal))

        xvec = unit_vector(calc_xvec(self.yvec, self.normal))

        return [xvec, self.yvec, self.normal]

    def get_points_in_lcsys_plane(self, p_dist: float = 1, plane: Plane = Plane.XY):
        csys = self.get_lcsys()
        p0 = self.point_in_plane
        vec_map = {
            Plane.XY: (0, 1),
            Plane.XZ: (0, 2),
            Plane.YZ: (1, 2),
        }
        i, j = vec_map.get(plane)
        vec2 = csys[i]
        vec3 = csys[j]

        p1 = p0 + vec2 * p_dist + vec3 * p_dist
        p2 = p0 - vec2 * p_dist + vec3 * p_dist
        p3 = p0 - vec2 * p_dist - vec3 * p_dist
        p4 = p0 + vec2 * p_dist - vec3 * p_dist
        return [p1, p2, p3, p4]

    def project_point_onto_plane(self, point: Iterable) -> np.ndarray:
        p = np.array(point)
        dist = p.dot(self.normal) + self.d
        return p - dist * self.normal

    def get_geom_as_part(self, name: str) -> Part:
        """Get the plane and origin of the"""
        from ada import Shape

        Shape()
        return
