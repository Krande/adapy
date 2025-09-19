from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.config import Config, logger
from ada.core.vector_utils import is_between_endpoints, is_parallel, vector_length
from ada.fem.elements import HingeProp
from ada.geom.direction import Direction

if TYPE_CHECKING:
    from ada import Beam, Node, Point
    from ada.api.connections import JointBase

_gen_point_tol = Config().general_point_tol


class BeamConnectionProps:
    def __init__(self, beam: Beam):
        self._beam = beam
        self._connected_to = []
        self._connected_end1: JointBase | None = None
        self._connected_end2: JointBase | None = None
        self._hinge_prop = None

    def calc_con_points(self, point_tol=_gen_point_tol) -> list[Point]:
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


class Justification(Enum):
    NA = "neutral axis"
    TOS = "top of steel"
    CUSTOM = "custom"


def get_offset_from_justification(beam: Beam, just: Justification) -> Direction:
    if just == Justification.NA:
        return Direction(0, 0, 0)
    elif just == Justification.TOS:
        return beam.up * beam.section.h / 2
    elif just == Justification.CUSTOM:
        pass
    else:
        raise ValueError(f"Unknown justification: {just}")


def is_on_beam(beam: Beam, point: Node) -> bool:
    """Returns if a node is on the beam axis including endpoints"""
    return point in beam.nodes or is_between_endpoints(point.p, beam.n1.p, beam.n2.p)


def split_beam(beam: Beam, point: Iterable | Node = None, fraction: float = None, length: float = None) -> Beam | None:
    """
    Split beam into two parts, and returns the new beam. Prioritizes input arguments in given order if  given
    multiple input.

    :param point:
    :param fraction: Fraction of the beam length from Node n1.
    :param length: Length of the beam from Node n1.
    """
    from ada import Beam, Node

    if isinstance(point, Node):
        point = point.p

    if point is not None:
        splitting_node = beam.get_node_on_beam_by_point(point)
    elif fraction is not None:
        splitting_node = beam.get_node_on_beam_by_fraction(fraction)
    elif length is not None:
        length_fraction = length / beam.length
        splitting_node = beam.get_node_on_beam_by_fraction(length_fraction)
    else:
        logger.warning(f"Beam {beam} is not split as inconclusive info is provided.")
        return None

    node_on_beam = beam.parent.fem.nodes.add(splitting_node)
    new_beam = Beam(
        name=f"{beam.name}_2",
        n1=node_on_beam,
        n2=beam.n2,
        sec=beam.section,
        mat=beam.material,
        up=beam.up,
        e1=beam.e1,
        e2=beam.e2,
        color=beam.color,
        parent=beam.parent,
        metadata=beam.metadata,
        units=beam.units,
    )

    beam.name = f"{beam.name}_1"
    beam.n2 = node_on_beam
    return new_beam


def get_beam_extensions(beam: Beam) -> Iterable[Beam]:
    """Returns connected beams with same material and section at beam end-nodes, that are parallel"""
    from ada import Beam

    def is_equal_beamtype(item) -> bool:
        return isinstance(item, Beam) and have_equivalent_props(beam, item) and is_parallel(beam.xvec, item.xvec)

    return list(filter(is_equal_beamtype, beam.n1.refs + beam.n2.refs))


def have_equivalent_props(beam: Beam, other_beam: Beam) -> bool:
    """Returns equivalent beam-type, meaning beam characteristics are the same but NOT the same beam"""
    sec_props_equal = beam.section.equal_props(other_beam.section)
    mat_props_equal = beam.material.model.equal_props(other_beam.material.model)
    return sec_props_equal and mat_props_equal and beam is not other_beam


def is_weak_axis_stiffened(beam: Beam, other_beam: Beam) -> bool:
    """Assumes rotation local z-vector (up) is weak axis"""
    return np.abs(np.dot(beam.up, other_beam.xvec)) < Config().general_point_tol and beam is not other_beam


def is_strong_axis_stiffened(beam: Beam, other_beam: Beam) -> bool:
    """Assumes rotation local y-vector is strong axis"""
    return np.abs(np.dot(beam.yvec, other_beam.xvec)) < Config().general_point_tol and beam is not other_beam


def get_justification(beam: Beam) -> Justification:
    """Justification line"""
    # Check if both self.e1 and self.e2 are None
    if beam.section.type in (beam.section.TYPES.TUBULAR, beam.section.TYPES.CIRCULAR):
        bm_height = beam.section.r * 2
    else:
        bm_height = beam.section.h

    if beam.e1 is None and beam.e2 is None:
        return Justification.NA
    elif beam.e1 is None or beam.e2 is None:
        return Justification.CUSTOM
    elif beam.e1.is_equal(beam.e2) and beam.e1.is_equal(beam.up * bm_height / 2):
        return Justification.TOS
    else:
        return Justification.CUSTOM


class NodeNotOnEndpointError(Exception):
    pass


def updating_nodes(beam: Beam, old_node: Node, new_node: Node) -> None:
    """Exchanging node on beam"""
    if old_node is beam.n1:
        beam.n1 = new_node
    elif old_node is beam.n2:
        beam.n2 = new_node
    else:
        raise NodeNotOnEndpointError(f"{old_node} is on either endpoint: {beam.nodes}")
