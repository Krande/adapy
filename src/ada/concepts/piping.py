from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Union

import numpy as np

from ada.base.physical_objects import BackendGeom
from ada.config import Settings as _Settings
from ada.core.utils import Counter, roundoff
from ada.core.vector_utils import angle_between, calc_zvec, unit_vector, vector_length
from ada.materials.utils import get_material
from ada.sections.utils import get_section

from .curves import ArcSegment
from .points import Node

if TYPE_CHECKING:
    from ada import Section


class Pipe(BackendGeom):
    def __init__(
        self,
        name,
        points,
        sec,
        mat="S355",
        content=None,
        metadata=None,
        colour=None,
        units="m",
        guid=None,
        ifc_elem=None,
    ):
        super().__init__(name, guid=guid, metadata=metadata, units=units, ifc_elem=ifc_elem)

        self._section, _ = get_section(sec)
        self._section.parent = self
        self._material = get_material(mat)
        self._content = content
        self.colour = colour

        self._n1 = points[0] if type(points[0]) is Node else Node(points[0], units=units)
        self._n2 = points[-1] if type(points[-1]) is Node else Node(points[-1], units=units)
        self._points = [Node(n, units=units) if type(n) is not Node else n for n in points]
        self._segments = []
        self._build_pipe()

    def _build_pipe(self):
        from ada.core.curve_utils import make_arc_segment

        segs = []
        for p1, p2 in zip(self.points[:-1], self.points[1:]):
            if vector_length(p2.p - p1.p) == 0.0:
                logging.info("skipping zero length segment")
                continue
            segs.append([p1, p2])
        segments = segs

        seg_names = Counter(prefix=self.name + "_")

        # Make elbows and adjust segments
        props = dict(section=self.section, material=self.material, parent=self, units=self.units)
        angle_tol = 1e-1
        len_tol = _Settings.point_tol if self.units == "m" else _Settings.point_tol * 1000
        for i, (seg1, seg2) in enumerate(zip(segments[:-1], segments[1:])):
            p11, p12 = seg1
            p21, p22 = seg2
            vlen1 = vector_length(seg1[1].p - seg1[0].p)
            vlen2 = vector_length(seg2[1].p - seg2[0].p)

            if vlen1 < len_tol or vlen2 == len_tol:
                logging.error(f'Segment Length is below point tolerance for unit "{self.units}". Skipping')
                continue

            xvec1 = unit_vector(p12.p - p11.p)
            xvec2 = unit_vector(p22.p - p21.p)
            a = angle_between(xvec1, xvec2)
            res = True if abs(abs(a) - abs(np.pi)) < angle_tol or abs(abs(a) - 0.0) < angle_tol else False

            if res is True:
                self._segments.append(PipeSegStraight(next(seg_names), p11, p12, **props))
            else:
                if p12 != p21:
                    logging.error("No shared point found")

                if i != 0 and len(self._segments) > 0:
                    pseg = self._segments[-1]
                    prev_p = (pseg.p1.p, pseg.p2.p)
                else:
                    prev_p = (p11.p, p12.p)
                try:
                    seg1, arc, seg2 = make_arc_segment(prev_p[0], prev_p[1], p22.p, self.pipe_bend_radius * 0.99)
                except ValueError as e:
                    logging.error(f"Error: {e}")  # , traceback: "{traceback.format_exc()}"')
                    continue
                except RuntimeError as e:
                    logging.error(f"Error: {e}")  # , traceback: "{traceback.format_exc()}"')
                    continue

                if i == 0 or len(self._segments) == 0:
                    self._segments.append(
                        PipeSegStraight(
                            next(seg_names), Node(seg1.p1, units=self.units), Node(seg1.p2, units=self.units), **props
                        )
                    )
                else:
                    if len(self._segments) == 0:
                        continue
                    pseg = self._segments[-1]
                    pseg.p2 = Node(seg1.p2, units=self.units)

                self._segments.append(
                    PipeSegElbow(
                        next(seg_names) + "_Elbow",
                        Node(seg1.p1, units=self.units),
                        Node(p21.p, units=self.units),
                        Node(seg2.p2, units=self.units),
                        arc.radius,
                        **props,
                        arc_seg=arc,
                    )
                )
                self._segments.append(
                    PipeSegStraight(
                        next(seg_names), Node(seg2.p1, units=self.units), Node(seg2.p2, units=self.units), **props
                    )
                )

    @property
    def segments(self) -> List[Union[PipeSegStraight, PipeSegElbow]]:
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
        w_tol = 0.125 if self.units == "m" else 125
        cor_tol = 0.003 if self.units == "m" else 3
        corr_t = (wt - (wt * w_tol)) - cor_tol
        d -= 2.0 * corr_t

        return roundoff(d + corr_t / 2.0)

    @property
    def section(self) -> Section:
        return self._section

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
        if value != self._units:
            self.n1.units = value
            self.n2.units = value
            self.section.units = value
            self.material.units = value
            self._segments = []
            for p in self.points:
                p.units = value
            self._build_pipe()
            self._units = value

    def get_ifc_elem(self):
        if self._ifc_elem is None:
            from ada.ifc.write.write_pipe import write_ifc_pipe

            self._ifc_elem = write_ifc_pipe(self)
        return self._ifc_elem

    def __repr__(self):
        return f"Pipe({self.name}, {self.section})"


class PipeSegStraight(BackendGeom):
    def __init__(
        self,
        name,
        p1,
        p2,
        section,
        material,
        parent=None,
        guid=None,
        metadata=None,
        units="m",
        colour=None,
        ifc_elem=None,
    ):
        super(PipeSegStraight, self).__init__(name, guid, metadata, units, parent, colour, ifc_elem=ifc_elem)
        self.p1 = p1
        self.p2 = p2
        self.section = section
        self.material = material

    @property
    def xvec1(self):
        return self.p2.p - self.p1.p

    @property
    def zvec(self):
        return calc_zvec(self.xvec1)

    @property
    def line(self):
        from ada.occ.utils import make_edge

        return make_edge(self.p1, self.p2)

    @property
    def shell(self):
        from ada.fem.shapes import ElemType
        from ada.occ.utils import sweep_pipe

        return sweep_pipe(self.line, self.xvec1, self.section.r, self.section.wt, ElemType.SHELL)

    @property
    def solid(self):
        from ada.fem.shapes import ElemType
        from ada.occ.utils import apply_penetrations, sweep_pipe

        raw_geom = sweep_pipe(self.line, self.xvec1, self.section.r, self.section.wt, ElemType.SOLID)

        geom = apply_penetrations(raw_geom, self.penetrations)
        return geom

    def _generate_ifc_elem(self):
        from ada.ifc.write.write_pipe import write_pipe_straight_seg

        return write_pipe_straight_seg(self)

    def __repr__(self):
        return f"PipeSegStraight({self.name}, p1={self.p1}, p2={self.p2})"


class PipeSegElbow(BackendGeom):
    def __init__(
        self,
        name,
        p1,
        p2,
        p3,
        bend_radius,
        section,
        material,
        parent=None,
        guid=None,
        metadata=None,
        units="m",
        colour=None,
        arc_seg=None,
    ):
        super(PipeSegElbow, self).__init__(name, guid, metadata, units, parent, colour)
        self.p1 = p1
        self.p2 = p2
        self.p3 = p3
        self.bend_radius = bend_radius
        self.section = section
        self.material = material
        self._arc_seg = arc_seg

    @property
    def parent(self) -> Pipe:
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def xvec1(self):
        return self.p2.p - self.p1.p

    @property
    def xvec2(self):
        return self.p3.p - self.p2.p

    @property
    def zvec(self):
        return calc_zvec(self.xvec1)

    @property
    def line(self):
        from ada.core.curve_utils import make_edges_and_fillet_from_3points

        if self.arc_seg.edge_geom is None:
            _, _, fillet = make_edges_and_fillet_from_3points(self.p1, self.p2, self.p3, self.bend_radius)
            edge = fillet
        else:
            edge = self.arc_seg.edge_geom
        return edge

    @property
    def shell(self):
        from ada.fem.shapes import ElemType
        from ada.occ.utils import sweep_pipe

        i = self.parent.segments.index(self)
        if i != 0:
            pseg = self.parent.segments[i - 1]
            xvec = pseg.xvec1
        else:
            xvec = self.xvec1

        return sweep_pipe(self.line, xvec, self.section.r, self.section.wt, ElemType.SHELL)

    @property
    def solid(self):
        from ada.fem.shapes import ElemType
        from ada.occ.utils import apply_penetrations, sweep_pipe

        i = self.parent.segments.index(self)
        if i != 0:
            pseg = self.parent.segments[i - 1]
            xvec = pseg.xvec1
        else:
            xvec = self.xvec1
        raw_geom = sweep_pipe(self.line, xvec, self.section.r, self.section.wt, ElemType.SOLID)

        geom = apply_penetrations(raw_geom, self.penetrations)
        return geom

    @property
    def arc_seg(self) -> ArcSegment:
        return self._arc_seg

    def _generate_ifc_elem(self):
        from ada.ifc.write.write_pipe import write_pipe_elbow_seg

        return write_pipe_elbow_seg(self)

    def __repr__(self):
        return f"PipeSegElbow({self.name}, r={self.bend_radius}, p1={self.p1}, p2={self.p2}, p3={self.p3})"
