from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar, Iterable, List, Union

import numpy as np
import pyquaternion as pq

import ada
from ada.core.vector_transforms import normal_to_points_in_plane, transform_3x3
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


class Placement:
    def __init__(self, origin: Iterable | Point = None, xdir=None, ydir=None, zdir=None, scale=1.0, parent=None):
        from ada.api.computed_placement import ComputedPlacement

        self._origin: Iterable | Point = origin

        # validate origin
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

        self._xdir: Iterable | Direction = xdir
        self._ydir: Iterable | Direction = ydir
        self._zdir: Iterable | Direction = zdir
        self._scale: float = scale
        self._parent = parent

        self._is_identity: bool = None
        self._computed_placement: ComputedPlacement = None

    def _init_computed_placement(self):
        """Lazy initialization of computed placement."""
        from ada.api.computed_placement import create_computed_placement_from_placement

        self._computed_placement = create_computed_placement_from_placement(self._xdir, self._ydir, self._zdir)

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

        if include_rotations:
            # Accumulate rotation matrices instead of quaternions
            accumulated_rot_matrix = self.rot_matrix.copy()
            ancestry = self.parent.get_ancestors(include_self=False)

            for ancestor in ancestry:
                current_location += ancestor.placement.origin
                # Matrix multiplication is faster than quaternion multiplication
                accumulated_rot_matrix = ancestor.placement.rot_matrix @ accumulated_rot_matrix

            # Extract direction vectors directly from the final rotation matrix
            return Placement(
                origin=current_location,
                xdir=accumulated_rot_matrix[0],
                ydir=accumulated_rot_matrix[1],
                zdir=accumulated_rot_matrix[2],
            )

        # For non-rotation case, just accumulate origins
        ancestry = self.parent.get_ancestors(include_self=False)
        for ancestor in ancestry:
            current_location += ancestor.placement.origin

        return Placement(origin=current_location, xdir=self.xdir, ydir=self.ydir, zdir=self.zdir)

    def rotate(self, axis: Iterable[float], angle: float) -> Placement:
        """Rotate the placement around an axis. Returns a new placement."""
        q0 = pq.Quaternion(matrix=self.rot_matrix)
        q = q0 * pq.Quaternion(axis=axis, angle=np.radians(angle))
        m = q.transformation_matrix

        return Placement(origin=self.origin, xdir=m[0, :3], ydir=m[1, :3], zdir=m[2, :3])

    @property
    def origin(self) -> Point:
        """Get origin using optimized caching."""
        return self._origin

    @origin.setter
    def origin(self, value):
        self._origin = value

    @property
    def xdir(self) -> Direction:
        """Get xdir using optimized caching."""
        if self._computed_placement is None:
            self._init_computed_placement()
        return self._computed_placement.xdir

    @property
    def ydir(self) -> Direction:
        """Get ydir using optimized caching."""
        if self._computed_placement is None:
            self._init_computed_placement()
        return self._computed_placement.ydir

    @property
    def zdir(self) -> Direction:
        """Get zdir using optimized caching."""
        if self._computed_placement is None:
            self._init_computed_placement()
        return self._computed_placement.zdir

    @property
    def scale(self):
        return self._scale

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @cached_property
    def rot_matrix(self):
        """Get rotation matrix using optimized caching."""
        # Fallback to original implementation
        return np.array([self.xdir, self.ydir, self.zdir])

    def get_matrix4x4(self):
        t = np.array([[self.origin[0]], [self.origin[1]], [self.origin[2]]])
        Rt = np.hstack([self.rot_matrix, t])
        return np.vstack([Rt, np.array([0.0, 0.0, 0.0, 1.0])])

    def transform_vector(self, vec: Iterable[float | int], inverse=False) -> np.ndarray:
        """Transform a vector using optimized caching."""
        if not isinstance(vec, np.ndarray):
            vec = np.array(vec)

        vec3d = transform_3x3(self.rot_matrix, np.array([vec]), inverse=inverse)
        return vec3d[0]

    def transform_array_from_other_place(
        self, arr: np.ndarray, other_place: Placement, ignore_translation=False
    ) -> np.ndarray:
        rotation_mat = self.rot_matrix @ np.linalg.inv(other_place.rot_matrix)

        if ignore_translation:
            transformed_vec = arr @ rotation_mat.T
        else:
            transformed_vec = (arr - other_place.origin) @ rotation_mat.T + self.origin
        return transformed_vec

    def transform_local_points_to_global(
        self, points2d: Iterable[Iterable[float | int, float | int]], inverse=False
    ) -> np.ndarray:
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
            axis=self.zdir.get_normalized(),
            ref_direction=self.xdir.get_normalized(),
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

    def __repr__(self):
        return (
            f"Placement(origin={self.origin}, xdir={self.xdir}, ydir={self.ydir}, zdir={self.zdir}, scale={self.scale})"
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

    def project_point_onto_plane(self, point: Iterable) -> ada.Point:
        p = np.array(point)
        dist = p.dot(self.normal) + self.d
        return ada.Point(p - dist * self.normal)

    def project_point_along_direction(self, point: Iterable, direction: Direction) -> ada.Point:
        """
        Project a point onto the plane along a given direction vector.

        Args:
            point: The point to project
            direction: The direction vector along which to project (Direction object)

        Returns:
            The projected point on the plane

        Raises:
            ValueError: If the direction is parallel to the plane (no intersection)
        """
        p = np.array(point)
        dir_vec = np.array(direction)

        # Check if direction is parallel to the plane
        dot_product = np.dot(dir_vec, self.normal)
        if abs(dot_product) < 1e-10:  # essentially zero
            raise ValueError("Direction vector is parallel to the plane - no intersection possible")

        # Calculate parameter t for the line equation: p + t * dir_vec
        # where the line intersects the plane: (p + t * dir_vec) · normal + d = 0
        t = -(np.dot(p, self.normal) + self.d) / dot_product

        # Calculate intersection point
        intersection = p + t * dir_vec

        return ada.Point(intersection)
