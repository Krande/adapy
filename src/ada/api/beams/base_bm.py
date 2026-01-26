from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Literal, TypeAlias, Union

import numpy as np

import ada.api.beams.geom_beams as geo_conv
from ada.api.bounding_box import BoundingBox
from ada.api.curves import LineSegment
from ada.api.nodes import Node, get_singular_node_by_volume
from ada.api.transforms import Placement
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.core.utils import Counter
from ada.core.vector_utils import is_between_endpoints, unit_vector, vector_length
from ada.fem.concept.constraints import DofType
from ada.geom import Geometry
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.materials import Material
from ada.materials.utils import get_material
from ada.sections import Section
from ada.sections.categories import BaseTypes
from ada.sections.string_to_section import interpret_section_str

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape

    from ada import Plate
    from ada.api.beams.helpers import BeamConnectionProps


section_counter = Counter(1)
material_counter = Counter(1)

# Define TypeAlias for BeamHinge types
BeamHingeConstraintType: TypeAlias = Literal["fixed", "free", "spring"]
_all_dofs = {"dx", "dy", "dz", "rx", "ry", "rz"}


@dataclass
class BeamHingeDofType:
    dof: DofType
    constraint_type: BeamHingeConstraintType
    spring_stiffness: float = 0.0

    def __post_init__(self):
        if self.dof not in _all_dofs:
            raise ValueError(
                f"Invalid dof_type: {self.constraint_type}. Must be one of 'dx', 'dy', 'dz', 'rx', 'ry', 'rz'."
            )


@dataclass
class BeamHinge:
    name: str
    dofs: list[BeamHingeDofType]

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if not isinstance(other, BeamHinge):
            return False
        return self.name == other.name

    @staticmethod
    def encastre(name: str, dof_type: BeamHingeConstraintType = "fixed") -> BeamHinge:
        """All 6 dofs are fixed"""
        dofs = []
        for dof in _all_dofs:
            dofs.append(BeamHingeDofType(dof, dof_type))
        return BeamHinge(name, dofs)

    @staticmethod
    def pinned(name: str) -> BeamHinge:
        """All 3 translational dofs are fixed, and all 3 rotational dofs are free."""
        dofs = []
        for dof in _all_dofs:
            if dof == "rx" or dof == "ry" or dof == "rz":
                dofs.append(BeamHingeDofType(dof, "free"))
            else:
                dofs.append(BeamHingeDofType(dof, "fixed"))
        return BeamHinge(name, dofs)


class Beam(BackendGeom):
    """
    The base Beam object

    :param n1: Start position of beam. List or Node object
    :param n2: End position of beam. List or Node object
    :param sec: Section definition. Str or Section Object
    :param mat: Material. Str or Material object. String: ['S355' & 'S420'] (default is 'S355' if None is parsed)
    :param name: Name of beam
    """

    def __init__(
        self,
        name,
        n1: Node | Iterable,
        n2: Node | Iterable,
        sec: str | Section,
        mat: str | Material = None,
        up=None,
        angle=0.0,
        e1=None,
        e2=None,
        units=Units.M,
        hi1: BeamHinge = None,
        hi2: BeamHinge = None,
        flush_offset_genie: bool = False,
        **kwargs,
    ):
        from ada.api.beams.helpers import BeamConnectionProps

        super().__init__(name, units=units, **kwargs)
        self._n1 = n1 if type(n1) is Node else Node(n1[:3], units=units)
        self._n2 = n2 if type(n2) is Node else Node(n2[:3], units=units)

        self._con_props = BeamConnectionProps(self)

        self._e1 = e1 if e1 is None else Direction(*e1)
        self._e2 = e2 if e2 is None else Direction(*e2)

        self._bbox = None

        # Section and Material setup
        if isinstance(sec, str):
            sec, _ = interpret_section_str(sec)

        self._section = sec
        self._section.refs.append(self)
        if self._section.parent is None:
            self._section.parent = self

        self._material = get_material(mat)
        self._material.refs.append(self)
        if self._material.parent is None:
            self._material.parent = self

        # Define orientations
        self._orientation = None
        self._init_orientation(angle, up)
        self._add_beam_to_node_refs()
        self._hi1 = hi1
        self._hi2 = hi2
        self._hash = None

        self._flush_offset_genie = bool(flush_offset_genie)

    @staticmethod
    def array_from_list_of_coords(
        list_of_coords: list[tuple | Point],
        sec: Section | str,
        mat: Material | str = None,
        name_gen: Iterable = None,
        make_closed=False,
    ) -> list[Beam]:
        """Create an array of beams from a list of coordinates"""
        beams = []
        ngen = name_gen if name_gen is not None else Counter(prefix="bm")
        for p1, p2 in zip(list_of_coords[:-1], list_of_coords[1:]):
            beams.append(Beam(next(ngen), p1, p2, sec, mat))

        p_start = list_of_coords[0]
        p_end = list_of_coords[-1]
        if isinstance(p_start, Point) and isinstance(p_end, Point):
            equal_end_start_point = p_start.is_equal(p_end)
        else:
            equal_end_start_point = list_of_coords[0] == list_of_coords[-1]

        if make_closed and not equal_end_start_point:
            beams.append(Beam(next(ngen), p_end, p_start, sec, mat))

        return beams

    @staticmethod
    def array_from_list_of_segments(
        segments: list[LineSegment],
        sec: Section | str,
        mat: Material | str = None,
        name_gen: Iterable = None,
        make_closed=False,
        up=None,
    ):
        beams = []
        ngen = name_gen if name_gen is not None else Counter(prefix="bm")
        for seg in segments:
            beams.append(Beam(next(ngen), seg.p1, seg.p2, sec, mat, up=up))

        if make_closed and segments[0].p1 != segments[-1].p2:
            beams.append(Beam(next(ngen), segments[-1].p2, segments[0].p1, sec, mat, up=up))

        return beams

    def _init_orientation(self, angle: float = 0.0, up: Iterable[float] | None = None) -> None:
        from ada.core.vector_transforms import compute_orientation

        # compute beam axis once
        xvec = unit_vector(self.n2.p - self.n1.p)
        key_x = tuple(xvec.tolist())
        key_up = tuple(up) if up is not None else None

        up_tup, y_tup, angle = compute_orientation(key_x, angle, key_up)

        self._orientation = Placement(self.n1.p, xvec, None, up_tup)
        self._xvec = self._orientation.xdir
        self._yvec = self._orientation.ydir
        self._up = self._orientation.zdir
        self._angle = angle

    def _local_axes_in_absolute(self):
        """
        Returns (xvec, yvec, up) expressed in the absolute/global system,
        respecting self.placement rotations (same logic as exporter).
        """
        from ada import Placement

        xvec = self.xvec
        yvec = self.yvec
        up = self.up

        if self.placement is not None and self.placement.is_identity() is False:
            ident_place = Placement()
            place_abs = self.placement.get_absolute_placement(include_rotations=True)

            # Only transform if rotation differs
            if not np.allclose(place_abs.rot_matrix, ident_place.rot_matrix):
                ori_vectors = place_abs.transform_array_from_other_place(
                    np.asarray([xvec, yvec, up]), ident_place, ignore_translation=True
                )
                xvec = ori_vectors[0]
                yvec = ori_vectors[1]
                up = ori_vectors[2]

        return xvec, yvec, up

    def _point_to_absolute(self, p: np.ndarray) -> np.ndarray:
        """
        Transforms a point p from the beam's local system into absolute/global,
        using self.placement. If identity, returns p unchanged.
        """
        from ada import Placement

        if self.placement is None or self.placement.is_identity():
            return p

        ident_place = Placement()
        place_abs = self.placement.get_absolute_placement(include_rotations=True)
        # include translation
        return place_abs.transform_array_from_other_place(np.asarray([p]), ident_place, ignore_translation=False)[0]

    def _curve_offset_local(self):
        """
        Compute local (x,y,z) curve offsets for Genie / COG, at end1 and end2.

        Returns a dict:
          {
            "end1": (ox1, oy1, oz1),
            "end2": (ox2, oy2, oz2),
            "avg":  (ox,  oy,  oz),   # average of end1/end2 (useful for COG)
            "is_varying": bool,       # True if end1 != end2
          }

        Notes:
        - Uses geometric centroid Cgy/Cgz.
        - Uses your sign convention: local offsets start from -e.
        """
        import numpy as np

        # --- e1/e2 -> numeric vectors ---
        e1 = np.array([self.e1.x, self.e1.y, self.e1.z], dtype=float) if self.e1 is not None else np.zeros(3)
        e2 = np.array([self.e2.x, self.e2.y, self.e2.z], dtype=float) if self.e2 is not None else np.zeros(3)

        # your sign convention
        off1 = -e1
        off2 = -e2

        # --- section geometric centroid data ---
        p = self.section.properties
        if getattr(p, "Cgy", None) is None or getattr(p, "Cgz", None) is None:
            raise ValueError(f"Section '{self.section.name}' missing geometric centroid (Cgy/Cgz).")

        Cgz = float(p.Cgz)
        h = float(self.section.h) if self.section.h is not None else None

        # Numeric offsets: place section relative to beam curve explicitly
        # Default: place curve at centroid (add centroid coords)
        # Special: your existing conventions for ANGULAR/TPROFILE
        if self.section.type == BaseTypes.ANGULAR:
            if h is None:
                raise ValueError("ANGULAR requires h to compute flush offset.")
            # flush-to-top: dz = (Cgz - h) = -ez
            dz = Cgz - h
            dy = 0
        elif self.section.type == BaseTypes.TPROFILE:
            if h is None:
                raise ValueError("TPROFILE requires h to compute offset.")
            dz = Cgz - h / 2.0
            dy = 0  # should be 0 for symmetrical profiles!
        # elif self.section.type == BaseTypes.IPROFILE and self.section.w_btn != self.section.w_top:
        #    logger.warning(f"IPROFILE with w_btn != w_top not yet supported. Using default offset.")
        #    dz = 0
        #    dy = 0
        else:
            dz = 0
            dy = 0

        add = np.array([0.0, dy, dz], dtype=float)
        off1 = off1 + add
        off2 = off2 + add

        is_varying = not np.allclose(off1, off2)
        avg = 0.5 * (off1 + off2)

        return {
            "end1": (float(off1[0]), float(off1[1]), float(off1[2])),
            "end2": (float(off2[0]), float(off2[1]), float(off2[2])),
            "avg": (float(avg[0]), float(avg[1]), float(avg[2])),
            "is_varying": bool(is_varying),
        }

    @property
    def flush_offset_genie(self) -> bool:
        """If True, apply Genie-style flush offsets"""
        return self._flush_offset_genie

    @flush_offset_genie.setter
    def flush_offset_genie(self, value: bool):
        self._flush_offset_genie = bool(value)

    @property
    def cog(self):
        """
        Beam COG in global coordinates.

        Conventions used here:
          - cog_line is midpoint of the beam line WITHOUT eccentricities.
          - Eccentricities e1/e2 are treated as offsets of the section/reference line
            relative to the beam line. If both ends exist and differ, we use their average
            for the COG (constant part).
          - Section geometric centroid uses Cgy/Cgz (not shear center).
          - For ANGULAR/TPROFILE we apply the same "flush-to-top" offset convention you use in Genie.
        """
        import warnings

        import numpy as np

        # Midpoint of beam line (no e)
        mid = self.cog_line.p if hasattr(self.cog_line, "p") else np.asarray(self.cog_line, dtype=float)

        data = self._curve_offset_local()  # numeric offsets for COG
        ox, oy, oz = data["avg"]

        if data["is_varying"]:
            warnings.warn(
                f"Beam '{self.name}': e1 != e2. COG uses average curve offset.",
                RuntimeWarning,
                stacklevel=2,
            )

        x_abs, y_abs, up_abs = self._local_axes_in_absolute()
        offset_abs = ox * np.asarray(x_abs, float) + oy * np.asarray(y_abs, float) + oz * np.asarray(up_abs, float)
        cog_abs = mid + offset_abs

        try:
            from ada import Point

            return Point(cog_abs)
        except Exception:
            return cog_abs

    @property
    def cog_line(self):
        """
        Midpoint of the beam line between n1 and n2 ONLY (no eccentricities).
        Returned in absolute/global coordinates (placement applied).
        """
        p1 = self.n1.p.copy()
        p2 = self.n2.p.copy()
        mid = 0.5 * (p1 + p2)

        mid_abs = self._point_to_absolute(mid)

        try:
            from ada import Point

            return Point(mid_abs, units=self.units)
        except Exception:
            return mid_abs

    def is_point_on_beam(self, point: Union[np.ndarray, Node]) -> bool:
        if isinstance(point, Node):
            point = point.p

        return is_between_endpoints(point, self.n1.p, self.n2.p, incl_endpoints=True)

    def get_node_on_beam_by_point(self, point: np.ndarray) -> Node:
        """Returns node on beam from point"""
        if not is_between_endpoints(point, self.n1.p, self.n2.p):
            raise ValueError(f"The node is not on line and between the beam end points, p: {point}, bm: {self}")

        return get_singular_node_by_volume(self.parent.fem.nodes, point)

    def get_node_on_beam_by_fraction(self, fraction: float) -> Node:
        """Returns node as a fraction of the beam length from n1-node."""

        if not 0.0 < fraction < 1.0:
            raise ValueError(f"Fraction {fraction} is not between 0 and 1")

        return get_singular_node_by_volume(self.parent.fem.nodes, self.n1.p + fraction * self.length * self.xvec)

    def get_outer_points_at_point(self, point: Point) -> list[Point]:
        from itertools import chain

        from ada.core.vector_transforms import local_2_global_points

        section_profile = self.section.get_section_profile(False)
        if section_profile.disconnected:
            ot = list(chain.from_iterable([x.points2d for x in section_profile.outer_curve_disconnected]))
        else:
            ot = section_profile.outer_curve.points2d

        yv = self.yvec
        xv = self.xvec

        return local_2_global_points(ot, point, yv, xv)

    def get_outer_points(self) -> tuple[list[Point], list[Point]]:
        """Returns outer points of beam"""
        p1 = self.n1.p + self.e1 if self.e1 is not None else self.n1.p
        p2 = self.n2.p + self.e2 if self.e2 is not None else self.n2.p
        nodes_p1 = self.get_outer_points_at_point(p1)
        nodes_p2 = self.get_outer_points_at_point(p2)

        return nodes_p1, nodes_p2

    def copy_to(
        self, name: str = None, p1=None, p2=None, rotation_axis: Iterable[float] = None, rotation_angle: float = None
    ) -> Beam:
        """Copy beam to new position"""
        if p1 is None and p2 is None:
            p1 = self.n1.p.copy()
            p2 = self.n2.p.copy()

        elif p2 is None and p1 is not None:
            p2 = p1 + self.length * self.xvec
        elif p1 is None and p2 is not None:
            p1 = p2 - self.length * self.xvec

        if name is None:
            name = self.name

        bm = Beam(name, p1, p2, sec=self.section.copy_to(), mat=self.material.copy_to())

        if rotation_axis is not None:
            if rotation_angle is None:
                raise ValueError("To apply rotation you also need to specify a rotation angle")

            bm.placement = bm.placement.rotate(rotation_axis, rotation_angle)
        else:
            if rotation_angle is not None:
                raise ValueError("To apply rotation you also need to specify a rotation axis")

        return bm

    def bbox(self) -> BoundingBox:
        """Bounding Box of beam"""
        if self._bbox is None:
            self._bbox = BoundingBox(self)

        return self._bbox

    def line_occ(self):
        from ada.occ.utils import make_wire_from_points

        points = [self.n1.p, self.n2.p]

        return make_wire_from_points(points)

    def shell_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.shell_geom())

    def solid_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        return geo_conv.straight_beam_to_geom(self)

    def shell_geom(self) -> Geometry:
        geom = geo_conv.straight_beam_to_geom(self, is_solid=False)
        return geom

    def _add_beam_to_node_refs(self) -> None:
        """Add beam to refs on nodes"""
        for beam_node in self.nodes:
            beam_node.add_obj_to_refs(self)

    def _remove_beam_from_node_refs(self) -> None:
        """Remove beam from refs on nodes"""
        for beam_node in self.nodes:
            beam_node.remove_obj_from_refs(self)

    def to_plates(self) -> list[Plate]:
        """Create a plate representation of the beam."""
        from ada import Counter, Plate

        sec = self.section
        if sec.type != sec.TYPES.BOX:
            raise ValueError("Only box sections can be converted to plates for now")

        pl_ng = Counter(prefix=f"bm_{self.name}_pl")
        plates = []
        start_p, end_p = self.get_outer_points()
        start_p = [*start_p, start_p[0]]
        end_p = [*end_p, end_p[0]]

        thick = [sec.t_ftop, sec.t_w, sec.t_fbtn, sec.t_w]
        for i, ((p1, p2), (p3, p4)) in enumerate(zip(zip(start_p[:-1], start_p[1:]), zip(end_p[:-1], end_p[1:]))):
            pl = Plate.from_3d_points(next(pl_ng), (p1, p2, p4, p3), thick[i], self.material, flip_normal=True)
            plates.append(pl)

        return plates

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if self._units == value:
            return

        self.n1.units = value
        self.n2.units = value
        self.section.units = value
        self.material.units = value
        for pen in self.booleans:
            pen.units = value

        self._units = value
        # Todo: incorporate change_type
        # self.change_type = ChangeAction.MODIFIED

    @property
    def section(self) -> Section:
        return self._section

    @section.setter
    def section(self, value: Section):
        old = self._section
        self._section = value
        self._section.refs.append(self)
        old.refs.remove(self)

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value: Material):
        # old = self._material
        self._material = value
        # self._material.refs.append(self)
        # if self in old.refs:
        #    old.refs.remove(self)

    @property
    def orientation(self) -> Placement:
        """This is the local orientation and position of the Beam within the local placement object"""
        return self._orientation

    @orientation.setter
    def orientation(self, value: Placement):
        self._orientation = value

    @property
    def member_type(self):
        from ada.core.vector_utils import is_parallel

        xvec = self.xvec
        if is_parallel(xvec, [0.0, 0.0, 1.0], tol=1e-1):
            mtype = "Column"
        elif xvec[2] == 0.0:
            mtype = "Girder"
        else:
            mtype = "Brace"

        return mtype

    @property
    def connection_props(self) -> BeamConnectionProps:
        return self._con_props

    @property
    def length(self) -> float:
        """Returns the length of the beam"""
        p1 = self.n1.p.copy()
        p2 = self.n2.p.copy()

        if self.e1 is not None:
            p1 += self.e1
        if self.e2 is not None:
            p2 += self.e2
        return vector_length(p2 - p1)

    @property
    def ori(self):
        """Get the x-vector, y-vector and z-vector of a given beam"""

        return self.xvec, self.yvec, self.up

    @property
    def xvec(self) -> Direction:
        """Local X-vector"""
        return self._xvec

    @property
    def yvec(self) -> Direction:
        """Local Y-vector"""
        return self._yvec

    @property
    def up(self) -> Direction:
        return self._up

    @up.setter
    def up(self, value: Direction):
        self._init_orientation(up=value)

    @property
    def xvec_e(self) -> Direction:
        """Local X-vector (including eccentricities)"""
        if self.e1 is not None:
            p1 = np.array([float(x) + float(self.e1[i]) for i, x in enumerate(self.n1.p.copy())])
        else:
            p1 = self.n1.p.copy()
        if self.e2 is not None:
            p2 = np.array([float(x) + float(self.e2[i]) for i, x in enumerate(self.n2.p.copy())])
        else:
            p2 = self.n2.p.copy()
        return Direction(*unit_vector(p2 - p1))

    @property
    def n1(self) -> Node:
        return self._n1

    @n1.setter
    def n1(self, new_node: Node):
        self._n1.remove_obj_from_refs(self)
        self._n1 = new_node  # .get_main_node_at_point()
        self._n1.add_obj_to_refs(self)

    @property
    def n2(self) -> Node:
        return self._n2

    @n2.setter
    def n2(self, new_node: Node):
        self._n2.remove_obj_from_refs(self)
        self._n2 = new_node  # .get_main_node_at_point()
        self._n2.add_obj_to_refs(self)

    @property
    def e1(self) -> Direction:
        return self._e1

    @e1.setter
    def e1(self, value: Iterable):
        self._e1 = Direction(*value)

    @property
    def e2(self) -> Direction:
        return self._e2

    @e2.setter
    def e2(self, value: Iterable):
        self._e2 = Direction(*value)

    @property
    def nodes(self) -> list[Node]:
        return [self.n1, self.n2]

    @property
    def angle(self) -> float:
        return self._angle

    @angle.setter
    def angle(self, value: float):
        self._init_orientation(value)

    @property
    def hinge1(self) -> BeamHinge:
        return self._hi1

    @hinge1.setter
    def hinge1(self, value: BeamHinge):
        self._hi1 = value

    @property
    def hinge2(self) -> BeamHinge:
        return self._hi2

    @hinge2.setter
    def hinge2(self, value: BeamHinge):
        self._hi2 = value

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(self.guid)
        return self._hash

    def __eq__(self, other: Beam):
        if not isinstance(other, Beam):
            return False
        return self.guid == other.guid

    def __setstate__(self, state):
        self.__dict__ = state

    def __getstate__(self):
        return self.__dict__

    def __repr__(self):
        p1s = self.n1.p.tolist()
        p2s = self.n2.p.tolist()
        secn = self.section.sec_str
        matn = self.material.name
        return f'{self.__class__.__name__}("{self.name}", {p1s}, {p2s}, "{secn}", "{matn}")'
