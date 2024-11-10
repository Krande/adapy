from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import ada
from ada.cadit.sat.utils import make_ints_if_possible


@dataclass
class SATEntity:
    id: int

    def to_string(self) -> str:
        raise NotImplementedError("Each entity must implement its string representation.")


@dataclass
class Body(SATEntity):
    lump: Lump
    bbox: list[float]

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        return f"-{self.id} body $-1 -1 -1 $-1 ${self.lump.id} $-1 $-1 T {bbox_str} #"


@dataclass
class Lump(SATEntity):
    shell: Shell
    body: Body
    bbox: list[float]
    next_lump: Lump = None

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        next_lump = -1 if self.next_lump is None else self.next_lump.id
        return f"-{self.id} lump $-1 -1 -1 $-1 ${next_lump} ${self.shell.id} ${self.body.id} T {bbox_str} #"


@dataclass
class Shell(SATEntity):
    face: Face
    bbox: list[float]

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        return f"-{self.id} shell $-1 -1 -1 $-1 $-1 $-1 ${self.face.id} $-1 $1 T {bbox_str} #"


@dataclass
class Face(SATEntity):
    loop: Loop
    shell: Shell
    name: StringAttribName
    surface: PlaneSurface

    def to_string(self) -> str:
        return f"-{self.id} face ${self.name.id} -1 -1 $-1 $-1 ${self.loop.id} ${self.shell.id} $-1 ${self.surface.id} forward double out F F #"


@dataclass
class Loop(SATEntity):
    coedge: CoEdge
    bbox: list[float]

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        return f"-{self.id} loop $-1 -1 -1 $-1 $-1 ${self.coedge.id} $3 T {bbox_str} unknown #"


@dataclass
class Vertex(SATEntity):
    edge: Edge
    point: SatPoint

    def to_string(self) -> str:
        return f"-{self.id} vertex $-1 -1 -1 $-1 ${self.edge.id} ${self.point.id} #"


@dataclass
class SatPoint(SATEntity):
    point: ada.Point

    def to_string(self) -> str:
        point_str = " ".join(str(x) for x in make_ints_if_possible(self.point))
        return f"-{self.id} point $-1 -1 -1 $-1 {point_str} #"


@dataclass
class CoEdge(SATEntity):
    next_coedge: CoEdge
    prev_coedge: CoEdge
    edge: Edge
    loop: Loop
    orientation: Literal["forward", "reverse"]

    def to_string(self) -> str:
        return f"-{self.id} coedge $-1 -1 -1 $-1 ${self.next_coedge.id} ${self.prev_coedge.id} $-1 ${self.edge.id} {self.orientation} ${self.loop.id} $-1 #"


@dataclass
class Edge(SATEntity):
    vertex_start: Vertex
    vertex_end: Vertex
    coedge: CoEdge
    straight_curve: StraightCurve

    start_pt: ada.Point
    end_pt: ada.Point

    def to_string(self) -> str:
        start_str = " ".join([str(x) for x in make_ints_if_possible(self.start_pt)])
        end_str = " ".join([str(x) for x in make_ints_if_possible(self.end_pt)])
        # pos_str = f"{self.start_pt[0]} {self.start_pt[1]} {self.start_pt[2]} {self.end_pt[0]} {self.end_pt[1]} {self.end_pt[2]}"
        vec = ada.Direction(self.end_pt - self.start_pt)
        length = vec.get_length()
        s1 = 0
        s2 = make_ints_if_possible([length])[0]
        return f"-{self.id} edge $-1 -1 -1 $-1 ${self.vertex_start.id} {s1} ${self.vertex_end.id} {s2} ${self.coedge.id} ${self.straight_curve.id} forward @7 unknown T {start_str} {end_str} #"


@dataclass
class StraightCurve(SATEntity):
    start_pt: ada.Point
    direction: ada.Direction

    def to_string(self) -> str:
        start_str = " ".join([str(x) for x in make_ints_if_possible(self.start_pt)])
        direction_str = " ".join([str(x) for x in make_ints_if_possible(self.direction.get_normalized())])
        return f"-{self.id} straight-curve $-1 -1 -1 $-1 {start_str} {direction_str} I I #"


@dataclass
class PlaneSurface(SATEntity):
    centroid: ada.Point
    normal: ada.Direction
    xvec: ada.Direction

    def to_string(self) -> str:
        centroid_str = " ".join([str(x) for x in make_ints_if_possible(self.centroid)])
        normal_str = " ".join([str(x) for x in make_ints_if_possible(self.normal)])
        xvec_str = " ".join([str(x) for x in make_ints_if_possible(self.xvec)])
        return f"-{self.id} plane-surface $-1 -1 -1 $-1 {centroid_str} {normal_str} {xvec_str} forward_v I I I I #"


@dataclass
class StringAttribName(SATEntity):
    name: str
    entity: SATEntity
    cache_attrib: CachedPlaneAttribute = None

    def to_string(self) -> str:
        cache_attrib = -1 if self.cache_attrib is None else self.cache_attrib.id
        return f"-{self.id} string_attrib-name_attrib-gen-attrib $-1 -1 ${cache_attrib} $-1 ${self.entity.id} 2 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 1 1 @6 dnvscp @12 {self.name} #"


@dataclass
class CachedPlaneAttribute(SATEntity):
    entity: SATEntity
    name: StringAttribName
    centroid: ada.Point
    normal: ada.Direction

    def to_string(self) -> str:
        centroid_str = " ".join([str(x) for x in make_ints_if_possible(self.centroid)])
        normal_str = " ".join([str(x) for x in make_ints_if_possible(self.normal)])
        return f"-{self.id} CachedPlaneAttribute-DNV-attrib $-1 -1 $-1 ${self.name.id} ${self.entity.id} 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 {centroid_str} {normal_str} 1 #"
