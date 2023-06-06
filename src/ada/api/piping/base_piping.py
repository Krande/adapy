from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

import numpy as np

from ada.api.beams.geom_beams import section_to_arbitrary_profile_def_with_voids
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.api.curves import ArcSegment
from ada.api.nodes import Node
from ada.config import Settings as _Settings
from ada.config import logger
from ada.core.utils import Counter, roundoff
from ada.core.vector_utils import angle_between, calc_zvec, unit_vector, vector_length
from ada.geom import Geometry
from ada.geom.placement import Direction, Axis2Placement3D, Axis1Placement
from ada.materials.utils import get_material
from ada.sections.utils import get_section

if TYPE_CHECKING:
    from ada import Section


class Pipe(BackendGeom):
    def __init__(
            self, name, points, sec, mat="S355", content=None, metadata=None, color=None, units: Units = Units.M,
            guid=None
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
        self._segments = build_pipe_segments(self)

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
            self._segments = build_pipe_segments(self)
            self._units = value

    def __repr__(self):
        return f"Pipe({self.name}, {self.section})"

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

    @property
    def line_occ(self):
        from ada.occ.utils import make_edge

        return make_edge(self.p1.p, self.p2.p)

    def shell_occ(self):
        from ada.fem.shapes import ElemType
        from ada.occ.utils import sweep_pipe

        return sweep_pipe(self.line_occ, self.xvec1, self.section.r, self.section.wt, ElemType.SHELL)

    def solid_occ(self):
        from ada.fem.shapes import ElemType
        from ada.occ.utils import apply_booleans, sweep_pipe

        raw_geom = sweep_pipe(self.line_occ, self.xvec1, self.section.r, self.section.wt, ElemType.SOLID)

        geom = apply_booleans(raw_geom, self.booleans)
        return geom

    def solid_geom(self) -> Geometry:
        from ada.api.beams.geom_beams import section_to_arbitrary_profile_def_with_voids
        from ada.geom.booleans import BooleanOperation
        import ada.geom.solids as geo_so

        profile = section_to_arbitrary_profile_def_with_voids(self.section)
        place = Axis2Placement3D(location=self.p1.p, axis=self.xvec1, ref_direction=self.zvec1)
        solid = geo_so.ExtrudedAreaSolid(profile, place, self.length, Direction(0, 0, 1))

        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def shell_geom(self) -> Geometry:
        raise NotImplementedError("shell_geom() not implemented")

    def __repr__(self):
        return f"PipeSegStraight({self.name}, p1={self.p1}, p2={self.p2})"


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
        from ada.fem.shapes import ElemType
        from ada.occ.utils import sweep_pipe

        i = self.parent.segments.index(self)
        if i != 0:
            pseg = self.parent.segments[i - 1]
            xvec = pseg.xvec1
        else:
            xvec = self.xvec1

        return sweep_pipe(self.line_occ(), xvec, self.section.r, self.section.wt, ElemType.SHELL)

    def solid_occ(self):
        from ada.fem.shapes import ElemType
        from ada.occ.utils import apply_booleans, sweep_pipe

        i = self.parent.segments.index(self)
        if i != 0:
            pseg = self.parent.segments[i - 1]
            xvec = pseg.xvec1
        else:
            xvec = self.xvec1
        raw_geom = sweep_pipe(self.line_occ(), xvec, self.section.r, self.section.wt, ElemType.SOLID)

        geom = apply_booleans(raw_geom, self.booleans)
        return geom

    def solid_geom(self) -> Geometry:
        from ada.geom.solids import RevolvedAreaSolid
        from ada.core.curve_utils import get_center_from_3_points_and_radius
        from ada.geom.booleans import BooleanOperation

        profile = section_to_arbitrary_profile_def_with_voids(self.section)
        position = Axis2Placement3D()

        cd = get_center_from_3_points_and_radius(self.p1, self.p2, self.p3, self.bend_radius, tol=1e-1)
        axis = Axis1Placement(location=cd.center, axis=self.xvec1)
        revolve_angle = np.rad2deg(angle_between(self.xvec1, self.xvec2))
        solid = RevolvedAreaSolid(profile, position, axis, revolve_angle)

        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def shell_geom(self) -> Geometry:
        raise NotImplementedError("shell_geom() not implemented")

    @property
    def arc_seg(self) -> ArcSegment:
        return self._arc_seg

    def __repr__(self):
        return f"PipeSegElbow({self.name}, r={self.bend_radius}, p1={self.p1}, p2={self.p2}, p3={self.p3})"


def build_pipe_segments(pipe: Pipe) -> list[PipeSegStraight | PipeSegElbow]:
    from ada.occ.utils import make_arc_segment_using_occ as make_arc_segment
    # from ada.api.curves import make_arc_segment

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

    len_tol = _Settings.point_tol if pipe.units == Units.M else _Settings.point_tol * 1000

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
        res = True if abs(abs(a) - abs(np.pi)) < angle_tol or abs(abs(a) - 0.0) < angle_tol else False

        if res is True:
            pipe_segments.append(PipeSegStraight(next(seg_names), p11, p12, **props))
            continue

        if p12 != p21:
            logger.error("No shared point found")

        if i != 0 and len(pipe_segments) > 0:
            pseg = pipe_segments[-1]
            prev_p = (pseg.p1.p, pseg.p2.p)
        else:
            prev_p = (p11.p, p12.p)

        try:
            seg1, arc, seg2 = make_arc_segment(prev_p[0], prev_p[1], p22.p, pipe.pipe_bend_radius * 0.99)
        except (ValueError, RuntimeError) as e:
            logger.error(f"Error: {e}")  # , traceback: "{traceback.format_exc()}"')
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
            PipeSegStraight(
                next(seg_names), Node(seg2.p1, units=pipe.units), Node(seg2.p2, units=pipe.units), **props
            )
        )

    return pipe_segments
