from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

import numpy as np

from ada.api.beams.geom_beams import section_to_arbitrary_profile_def_with_voids
from ada.api.curves import ArcSegment
from ada.api.nodes import Node
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.config import Config, logger
from ada.core.exceptions import VectorNormalizeError
from ada.core.utils import Counter, roundoff
from ada.core.vector_utils import angle_between, calc_zvec, unit_vector, vector_length
from ada.geom import Geometry
from ada.geom.placement import Axis1Placement, Axis2Placement3D, Direction
from ada.materials.utils import get_material
from ada.sections.utils import get_section

if TYPE_CHECKING:
    from ada import Material, Section


class Pipe(BackendGeom):
    def __init__(
        self, name, points, sec, mat="S355", content=None, metadata=None, color=None, units: Units = Units.M, guid=None
    ):
        super().__init__(name, color=color, guid=guid, metadata=metadata, units=units)

        self._section, _ = get_section(sec)
        self._section.parent = self
        self._material = get_material(mat)
        self._content = content

        self.section.refs.append(self)
        self.material.refs.append(self)

        self._n1 = points[0] if type(points[0]) is Node else Node(points[0], units=units)
        self._n2 = points[-1] if type(points[-1]) is Node else Node(points[-1], units=units)
        self._points = [Node(n, units=units) if type(n) is not Node else n for n in points]
        # self._segments = build_pipe_segments(self)
        self._segments = build_pipe_segments_alt(self)

    @property
    def segments(self) -> list[PipeSegStraight | PipeSegElbow]:
        return self._segments

    @property
    def material(self):
        return self._material

    @material.setter
    def material(self, value):
        self._material = value

    @property
    def points(self):
        return self._points

    @property
    def start(self):
        return self.points[0]

    @property
    def end(self):
        return self.points[-1]

    @property
    def metadata(self):
        return self._metadata

    @property
    def pipe_bend_radius(self):
        wt = self.section.wt
        r = self.section.r
        d = r * 2
        w_tol = 0.125 if self.units == Units.M else 125
        cor_tol = 0.003 if self.units == Units.M else 3
        corr_t = (wt - (wt * w_tol)) - cor_tol
        d -= 2.0 * corr_t

        return roundoff(d + corr_t / 2.0)

    @property
    def section(self) -> Section:
        return self._section

    @section.setter
    def section(self, value):
        self._section = value

    @property
    def n1(self) -> Node:
        return self._n1

    @property
    def n2(self) -> Node:
        return self._n2

    @property
    def nodes(self) -> list[Node]:
        return [self.n1, self.n2]

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            self.n1.units = value
            self.n2.units = value
            self.section.units = value
            self.material.units = value
            for p in self.points:
                p.units = value
            self._segments = build_pipe_segments_alt(self)
            self._units = value

    def __repr__(self):
        points = [x.p.tolist() for x in self.points]
        return f'Pipe("{self.name}", {points}, {self.section.name})'

    @staticmethod
    def from_segments(name: str, segments: list[PipeSegStraight | PipeSegElbow]) -> Pipe:
        points = list(chain.from_iterable([(Node(x.p1), Node(x.p2)) for x in segments]))
        seg0 = segments[0]
        seg0_section = seg0.section
        seg0_material = seg0.material

        pipe = Pipe(name, points, seg0_section, seg0_material)
        for i, seg in enumerate(segments):
            pipe.segments[i].guid = seg.guid

        return pipe


class PipeSegStraight(BackendGeom):
    def __init__(
        self, name, p1, p2, section, material, parent=None, guid=None, metadata=None, units=Units.M, color=None
    ):
        super(PipeSegStraight, self).__init__(
            name=name, guid=guid, metadata=metadata, units=units, parent=parent, color=color
        )
        self.p1 = p1 if isinstance(p1, Node) else Node(p1, units=units)
        self.p2 = p2 if isinstance(p2, Node) else Node(p2, units=units)
        self._xvec1 = unit_vector(self.p2.p - self.p1.p)
        self._zvec1 = calc_zvec(self._xvec1)
        self.section = section
        self.material = material
        section.refs.append(self)
        material.refs.append(self)

    @property
    def xvec1(self):
        return self._xvec1

    @property
    def zvec1(self):
        return self._zvec1

    @property
    def length(self):
        return vector_length(self.p2.p - self.p1.p)

    def line_occ(self):
        from ada.occ.utils import make_edge

        return make_edge(self.p1.p, self.p2.p)

    def shell_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.shell_geom())

    def solid_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        import ada.geom.solids as geo_so
        from ada.api.beams.geom_beams import section_to_arbitrary_profile_def_with_voids
        from ada.geom.booleans import BooleanOperation

        profile = section_to_arbitrary_profile_def_with_voids(self.section)
        place = Axis2Placement3D(location=self.p1.p, axis=self.xvec1, ref_direction=self.zvec1)
        solid = geo_so.ExtrudedAreaSolid(profile, place, self.length, Direction(0, 0, 1))

        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def shell_geom(self) -> Geometry:
        geom = self.solid_geom()
        profile = section_to_arbitrary_profile_def_with_voids(self.section, solid=False)
        geom.geometry.swept_area = profile

        return geom

    def __repr__(self):
        return f"PipeSegStraight({self.name}, p1={self.p1}, p2={self.p2}, section={self.section.name})"


class PipeSegElbow(BackendGeom):
    def __init__(
        self,
        name,
        start,
        midpoint,
        end,
        bend_radius,
        section,
        material,
        parent=None,
        guid=None,
        metadata=None,
        units=Units.M,
        color=None,
        arc_seg=None,
    ):
        super(PipeSegElbow, self).__init__(
            name=name, guid=guid, metadata=metadata, units=units, parent=parent, color=color
        )
        if not isinstance(start, Node):
            start = Node(start, units=units)
        if not isinstance(midpoint, Node):
            midpoint = Node(midpoint, units=units)
        if not isinstance(end, Node):
            end = Node(end, units=units)

        self.p1 = start
        self.p2 = midpoint
        self.p3 = end
        self.bend_radius = bend_radius
        self.section = section
        self.material = material
        self._arc_seg = arc_seg
        self._xvec1 = Direction(*(self.p2.p - self.p1.p))
        self._xvec2 = Direction(*(self.p3.p - self.p2.p))
        self._zvec1 = Direction(*calc_zvec(self._xvec1))
        section.refs.append(self)
        material.refs.append(self)

    @staticmethod
    def from_arc_segment(name, arc: ArcSegment, section: Section, material: Material, **kwargs) -> PipeSegElbow:
        return PipeSegElbow(
            name, arc.p1, arc.midpoint, arc.p2, arc.radius, section=section, material=material, arc_seg=arc, **kwargs
        )

    @property
    def parent(self) -> Pipe:
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def xvec1(self):
        return self._xvec1

    @property
    def xvec2(self):
        return self._xvec2

    @property
    def zvec1(self):
        return self._zvec1

    def line_occ(self):
        from ada.occ.utils import make_edges_and_fillet_from_3points_using_occ

        if self.arc_seg.edge_geom is None:
            _, _, fillet = make_edges_and_fillet_from_3points_using_occ(self.p1, self.p2, self.p3, self.bend_radius)
            edge = fillet
        else:
            edge = self.arc_seg.edge_geom
        return edge

    def shell_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.shell_geom())

    def solid_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.booleans import BooleanOperation
        from ada.geom.solids import RevolvedAreaSolid

        profile = section_to_arbitrary_profile_def_with_voids(self.section)

        xvec1 = unit_vector(self.arc_seg.s_normal)
        xvec2 = unit_vector(self.arc_seg.e_normal)
        normal = unit_vector(calc_zvec(xvec2, xvec1))

        position = Axis2Placement3D(self.p1, xvec1, normal)

        axis = Axis1Placement(location=self.arc_seg.center, axis=normal)

        revolve_angle = 180 - np.rad2deg(angle_between(xvec1, xvec2))
        solid = RevolvedAreaSolid(profile, position, axis, revolve_angle)

        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def shell_geom(self) -> Geometry:
        geom = self.solid_geom()
        profile = section_to_arbitrary_profile_def_with_voids(self.section, solid=False)
        geom.geometry.swept_area = profile

        return geom

    @property
    def arc_seg(self) -> ArcSegment:
        return self._arc_seg

    def __repr__(self):
        return f"PipeSegElbow({self.name}, r={self.bend_radius}, p1={self.p1}, p2={self.p2}, p3={self.p3})"


def build_pipe_segments(pipe: Pipe) -> list[PipeSegStraight | PipeSegElbow]:
    from ada.occ.utils import make_arc_segment_using_occ

    segs = []
    for p1, p2 in zip(pipe.points[:-1], pipe.points[1:]):
        if vector_length(p2.p - p1.p) == 0.0:
            logger.info("skipping zero length segment")
            continue
        segs.append([p1, p2])
    segments = segs

    seg_names = Counter(prefix=pipe.name + "_")

    # Make elbows and adjust segments
    props = dict(section=pipe.section, material=pipe.material, parent=pipe, units=pipe.units)
    angle_tol = 1e-1

    len_tol = Config().general_point_tol if pipe.units == Units.M else Config().general_point_tol * 1000

    pipe_segments = []
    if len(segments) == 1:
        seg_s, seg_e = segments[0]
        pipe_segments.append(PipeSegStraight(next(seg_names), seg_s, seg_e, **props))

    for i, (seg1, seg2) in enumerate(zip(segments[:-1], segments[1:])):
        p11, p12 = seg1
        p21, p22 = seg2
        vlen1 = vector_length(seg1[1].p - seg1[0].p)
        vlen2 = vector_length(seg2[1].p - seg2[0].p)

        if vlen1 < len_tol or vlen2 == len_tol:
            logger.error(f'Segment Length is below point tolerance for unit "{pipe.units}". Skipping')
            continue

        xvec1 = unit_vector(p12.p - p11.p)
        xvec2 = unit_vector(p22.p - p21.p)
        a = angle_between(xvec1, xvec2)
        segments_are_parallel = True if abs(abs(a) - abs(np.pi)) < angle_tol or abs(abs(a) - 0.0) < angle_tol else False

        if segments_are_parallel:
            pipe_segments.append(PipeSegStraight(next(seg_names), p11, p12, **props))
            continue

        if p12 != p21:
            logger.error("No shared point found")

        if i != 0 and len(pipe_segments) > 0:
            pseg = pipe_segments[-1]
            prev_p = (pseg.p1.p, pseg.p2.p)
        else:
            prev_p = (p11.p, p12.p)

        radius = pipe.pipe_bend_radius
        arc_start = prev_p[0]
        arc_center = prev_p[1]
        arc_end = p22.p
        try:
            seg1, arc, seg2 = make_arc_segment_using_occ(arc_start, arc_center, arc_end, radius)
        except (ValueError, VectorNormalizeError) as e:
            points = [arc_start.tolist(), arc_center.tolist(), arc_end.tolist()]
            logger.error(f"Arc build failed for {pipe} points: {points}. Error: {e}")
            continue

        if i == 0 or len(pipe_segments) == 0:
            pipe_segments.append(
                PipeSegStraight(
                    next(seg_names), Node(seg1.p1, units=pipe.units), Node(seg1.p2, units=pipe.units), **props
                )
            )
        else:
            if len(pipe_segments) == 0:
                continue
            pseg = pipe_segments[-1]
            pseg.p2 = Node(seg1.p2, units=pipe.units)

        pipe_segments.append(
            PipeSegElbow(
                next(seg_names) + "_Elbow",
                Node(seg1.p1, units=pipe.units),
                Node(p21.p, units=pipe.units),
                Node(seg2.p2, units=pipe.units),
                arc.radius,
                **props,
                arc_seg=arc,
            )
        )
        pipe_segments.append(
            PipeSegStraight(next(seg_names), Node(seg2.p1, units=pipe.units), Node(seg2.p2, units=pipe.units), **props)
        )

    return pipe_segments


def build_pipe_segments_alt(pipe: Pipe) -> list[PipeSegStraight | PipeSegElbow]:
    from ada.core.curve_utils import segments3d_from_points3d

    seg_names = Counter(prefix=pipe.name + "_")
    props = dict(section=pipe.section, material=pipe.material, parent=pipe, units=pipe.units)
    angle_tol = 1e-1
    len_tol = Config().general_point_tol if pipe.units == Units.M else Config().general_point_tol * 1000
    segments = segments3d_from_points3d(pipe.points, radius=pipe.pipe_bend_radius, angle_tol=angle_tol, len_tol=len_tol)
    pipe_segments = []
    for segment in segments:
        if isinstance(segment, ArcSegment):
            seg = PipeSegElbow.from_arc_segment(next(seg_names), segment, **props)
        else:
            seg = PipeSegStraight(next(seg_names), segment.p1, segment.p2, **props)

        pipe_segments.append(seg)

    return pipe_segments


def make_elbow() -> tuple[PipeSegStraight, PipeSegElbow, PipeSegStraight] | None: ...
