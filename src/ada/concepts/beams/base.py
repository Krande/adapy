from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterable, Union

import numpy as np

import ada.concepts.beams.geom_conversion as geo_conv
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.concepts.bounding_box import BoundingBox
from ada.concepts.curves import CurvePoly, CurveRevolve
from ada.concepts.nodes import Node, get_singular_node_by_volume
from ada.config import Settings, logger
from ada.core.utils import Counter, roundoff
from ada.core.vector_utils import (
    angle_between,
    calc_yvec,
    calc_zvec,
    is_between_endpoints,
    unit_vector,
    vector_length,
)
from ada.geom import Geometry
from ada.geom.placement import Direction
from ada.materials import Material
from ada.materials.utils import get_material
from ada.sections import Section
from ada.sections.utils import interpret_section_str

from .helpers import Justification

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape

    from ada.concepts.connections import JointBase
    from ada.fem.elements import HingeProp

section_counter = Counter(1)
material_counter = Counter(1)


class BeamConnectionProps:
    def __init__(self, beam: Beam):
        self._beam = beam
        self._connected_to = []
        self._connected_end1 = None
        self._connected_end2 = None
        self._hinge_prop = None

    def calc_con_points(self, point_tol=Settings.point_tol):
        from ada.core.vector_utils import sort_points_by_dist

        a = self._beam.n1.p
        b = self._beam.n2.p
        points = [tuple(con.centre) for con in self.connected_to]

        def is_mem_eccentric(mem, centre):
            is_ecc = False
            end = None
            if point_tol < vector_length(mem.n1.p - centre) < mem.length * 0.9:
                is_ecc = True
                end = mem.n1.p
            if point_tol < vector_length(mem.n2.p - centre) < mem.length * 0.9:
                is_ecc = True
                end = mem.n2.p
            return is_ecc, end

        if len(self.connected_to) == 1:
            con = self.connected_to[0]
            if con.main_mem == self:
                for m in con.beams:
                    if m != self:
                        is_ecc, end = is_mem_eccentric(m, con.centre)
                        if is_ecc:
                            logger.info(f'do something with end "{end}"')
                            points.append(tuple(end))

        midpoints = []
        prev_p = None
        for p in sort_points_by_dist(a, points):
            p = np.array(p)
            bmlen = self._beam.length
            vlena = vector_length(p - a)
            vlenb = vector_length(p - b)

            if prev_p is not None:
                if vector_length(p - prev_p) < point_tol:
                    continue

            if vlena < point_tol:
                self._connected_end1 = self.connected_to[points.index(tuple(p))]
                prev_p = p
                continue

            if vlenb < point_tol:
                self._connected_end2 = self.connected_to[points.index(tuple(p))]
                prev_p = p
                continue

            if vlena > bmlen or vlenb > bmlen:
                prev_p = p
                continue

            midpoints += [p]
            prev_p = p

        return midpoints

    @property
    def connected_to(self) -> list[JointBase]:
        return self._connected_to

    @property
    def connected_end1(self):
        return self._connected_end1

    @property
    def connected_end2(self):
        return self._connected_end2

    @property
    def hinge_prop(self) -> HingeProp:
        return self._hinge_prop

    @hinge_prop.setter
    def hinge_prop(self, value: HingeProp):
        value.beam_ref = self
        if value.end1 is not None:
            value.end1.concept_node = self._beam.n1
        if value.end2 is not None:
            value.end2.concept_node = self._beam.n2
        self._hinge_prop = value


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
            **kwargs,
    ):
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
        self._section.parent = self

        self._material = get_material(mat)
        self._material.refs.append(self)

        # Define orientations
        self._init_orientation(angle, up)
        self.add_beam_to_node_refs()

    @staticmethod
    def from_list_of_coords(
            list_of_coords: list[tuple], sec: Section | str, mat: Material | str = None, name_gen: Callable = None
    ) -> list[Beam]:
        beams = []
        ngen = name_gen if name_gen is not None else Counter(prefix="bm")
        for p1, p2 in zip(list_of_coords[:-1], list_of_coords[1:]):
            beams.append(Beam(next(ngen), p1, p2, sec, mat))
        return beams

    def _init_orientation(self, angle=None, up=None) -> None:
        xvec = unit_vector(self.n2.p - self.n1.p)
        tol = 1e-3
        zvec = calc_zvec(xvec)
        gup = np.array(zvec)

        if up is None:
            if angle != 0.0 and angle is not None:
                from pyquaternion import Quaternion

                my_quaternion = Quaternion(axis=xvec, degrees=angle)
                rot_mat = my_quaternion.rotation_matrix
                up = np.array([roundoff(x) if abs(x) != 0.0 else 0.0 for x in np.matmul(gup, np.transpose(rot_mat))])
            else:
                up = np.array([roundoff(x) if abs(x) != 0.0 else 0.0 for x in gup])
            yvec = calc_yvec(xvec, up)
        else:
            if (len(up) == 3) is False:
                raise ValueError("Up vector must be length 3")
            if vector_length(xvec - up) < tol:
                raise ValueError("The assigned up vector is too close to your beam direction")
            yvec = calc_yvec(xvec, up)
            # TODO: Fix improper calculation of angle (e.g. xvec = [1,0,0] and up = [0,1,0] should be 270?
            rad = angle_between(up, zvec)
            angle = np.rad2deg(rad)
            up = np.array(up)

        # lup = np.cross(xvec, yvec)
        self._xvec = Direction(*xvec)
        self._yvec = Direction(*[roundoff(x) for x in unit_vector(yvec)])
        self._up = Direction(*up)
        self._angle = angle

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

    def updating_nodes(self, old_node: Node, new_node: Node) -> None:
        """Exchanging node on beam"""
        if old_node is self.n1:
            self.n1 = new_node
        elif old_node is self.n2:
            self.n2 = new_node
        else:
            raise NodeNotOnEndpointError(f"{old_node} is on either endpoint: {self.nodes}")

    def get_outer_points(self):
        from itertools import chain

        from ada.core.vector_utils import local_2_global_points

        section_profile = self.section.get_section_profile(False)
        if section_profile.disconnected:
            ot = list(chain.from_iterable([x.points2d for x in section_profile.outer_curve_disconnected]))
        else:
            ot = section_profile.outer_curve.points2d

        yv = self.yvec
        xv = self.xvec
        p1 = self.n1.p
        p2 = self.n2.p

        nodes_p1 = local_2_global_points(ot, p1, yv, xv)
        nodes_p2 = local_2_global_points(ot, p2, yv, xv)

        return nodes_p1, nodes_p2

    def copy_to(self, p1, p2, name: str) -> Beam:
        return Beam(name, p1, p2, sec=self.section, mat=self.material)

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

    @property
    def section(self) -> Section:
        return self._section

    @section.setter
    def section(self, value: Section):
        self._section = value

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value: Material):
        self._material = value

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

    def bbox(self) -> BoundingBox:
        """Bounding Box of beam"""
        if self._bbox is None:
            self._bbox = BoundingBox(self)

        return self._bbox

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

    def line(self):
        from ada.occ.utils import make_wire_from_points

        points = [self.n1.p, self.n2.p]

        return make_wire_from_points(points)

    def shell(self) -> TopoDS_Shape:
        from ada.occ.utils import apply_booleans, create_beam_geom

        geom = apply_booleans(create_beam_geom(self, False), self.booleans)

        return geom

    def solid(self) -> TopoDS_Shape:
        from ada.occ.utils import apply_booleans, create_beam_geom

        geom = apply_booleans(create_beam_geom(self, True), self.booleans)

        return geom

    def solid_geom(self) -> Geometry:
        return geo_conv.straight_beam_to_geom(self)

    def shell_geom(self) -> Geometry:
        geom = geo_conv.straight_beam_to_geom(self, is_solid=False)
        return geom

    @property
    def nodes(self) -> list[Node]:
        return [self.n1, self.n2]

    @property
    def angle(self) -> float:
        return self._angle

    @angle.setter
    def angle(self, value: float):
        self._init_orientation(value)

    def add_beam_to_node_refs(self) -> None:
        """Add beam to refs on nodes"""
        for beam_node in self.nodes:
            beam_node.add_obj_to_refs(self)

    def remove_beam_from_node_refs(self) -> None:
        """Remove beam from refs on nodes"""
        for beam_node in self.nodes:
            beam_node.remove_obj_from_refs(self)

    def __hash__(self):
        return hash(self.guid)

    def __eq__(self, other: Beam):
        for key, val in self.__dict__.items():
            if "parent" in key or key in ["_ifc_settings", "_ifc_elem"]:
                continue
            oval = other.__dict__[key]

            if type(val) in (list, tuple, np.ndarray):
                if False in [x == y for x, y in zip(oval, val)]:
                    return False
            try:
                res = oval != val
            except ValueError as e:
                logger.error(e)
                return True

            if res is True:
                return False

        return True

    def __repr__(self):
        p1s = self.n1.p.tolist()
        p2s = self.n2.p.tolist()
        secn = self.section.sec_str
        matn = self.material.name
        return f'Beam("{self.name}", {p1s}, {p2s}, "{secn}", "{matn}")'

    def __setstate__(self, state):
        self.__dict__ = state

    def __getstate__(self):
        return self.__dict__


class BeamTapered(Beam):
    def __init__(self, name, n1: Iterable, n2: Iterable, sec: str | Section, tap: str | Section = None, **kwargs):
        super().__init__(name=name, n1=n1, n2=n2, sec=sec, **kwargs)

        if isinstance(sec, str) and tap is None:
            _, tap = interpret_section_str(sec)

        if isinstance(tap, str):
            _, tap = interpret_section_str(tap)

        self._taper = tap
        self._taper.refs.append(self)
        self._taper.parent = self

    @property
    def taper(self) -> Section:
        return self._taper

    @taper.setter
    def taper(self, value: Section):
        self._taper = value

    def solid_geom(self) -> Geometry:
        return geo_conv.straight_tapered_beam_to_geom(self)


class BeamSweep(Beam):
    def __init__(self, name: str, curve: CurvePoly, sec: str | Section, **kwargs):
        n1 = curve.points3d[0]
        n2 = curve.points3d[-1]
        super().__init__(name=name, n1=n1, n2=n2, sec=sec, **kwargs)
        self._curve = curve
        curve.parent = self

    @property
    def curve(self) -> CurvePoly:
        return self._curve

    def solid_geom(self) -> Geometry:
        return geo_conv.swept_beam_to_geom(self)


class BeamRevolve(Beam):
    def __init__(self, name: str, curve: CurveRevolve, sec: str | Section, **kwargs):
        n1 = curve.p1
        n2 = curve.p2
        super().__init__(name=name, n1=n1, n2=n2, sec=sec, **kwargs)
        self._curve = curve
        curve.parent = self

    @property
    def curve(self) -> CurveRevolve:
        return self._curve

    def solid_geom(self) -> Geometry:
        return geo_conv.revolved_beam_to_geom(self)


class NodeNotOnEndpointError(Exception):
    pass
