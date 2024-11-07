from dataclasses import dataclass
from typing import Literal

import ada
from ada.cadit.sat.utils import make_ints_if_possible


@dataclass
class SATEntity:
    entity_id: int

    def to_string(self) -> str:
        raise NotImplementedError("Each entity must implement its string representation.")


@dataclass
class Body(SATEntity):
    lump_id: int
    bbox: list[float]

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        return f"-{self.entity_id} body $-1 -1 -1 $-1 $1 $-1 $-1 T {bbox_str} #"


@dataclass
class Lump(SATEntity):
    shell_id: int
    body_id: int
    bbox: list[float]

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        return f"-{self.entity_id} lump $-1 -1 -1 $-1 $-1 ${self.shell_id} ${self.body_id} T {bbox_str} #"


@dataclass
class Shell(SATEntity):
    face_id: int
    bbox: list[float]

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        return f"-{self.entity_id} shell $-1 -1 -1 $-1 $-1 $-1 ${self.face_id} $-1 $1 T {bbox_str} #"


@dataclass
class Face(SATEntity):
    loop_id: int
    shell_id: int
    name_id: int
    surface_id: int

    def to_string(self) -> str:
        return f"-{self.entity_id} face ${self.name_id} -1 -1 $-1 $-1 ${self.loop_id} ${self.shell_id} $-1 ${self.surface_id} forward double out F F #"


@dataclass
class Loop(SATEntity):
    coedge_id: int
    bbox: list[float]

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        return f"-{self.entity_id} loop $-1 -1 -1 $-1 $-1 ${self.coedge_id} $3 T {bbox_str} unknown #"


@dataclass
class Vertex(SATEntity):
    edge_id: int
    point_id: int


    def to_string(self) -> str:
        return f"-{self.entity_id} vertex $-1 -1 -1 $-1 ${self.edge_id} ${self.point_id} #"


@dataclass
class SatPoint(SATEntity):
    point: ada.Point

    def to_string(self) -> str:
        return f"-{self.entity_id} point $-1 -1 -1 $-1 {self.point.x} {self.point.y} {self.point.z} #"

@dataclass
class CoEdge(SATEntity):
    next_coedge: int
    prev_coedge: int
    edge_id: int
    loop_id: int
    orientation: Literal["forward", "reverse"]

    def to_string(self) -> str:
        return f"-{self.entity_id} coedge $-1 -1 -1 $-1 ${self.next_coedge} ${self.prev_coedge} $-1 ${self.edge_id} {self.orientation} ${self.loop_id} $-1 #"

@dataclass
class Edge(SATEntity):
    vertex_start_id: int
    vertex_end_id: int
    coedge_id: int
    straight_curve_id: int

    start_pt: ada.Point
    end_pt: ada.Point

    def to_string(self) -> str:
        start_str = ' '.join([str(x) for x in make_ints_if_possible(self.start_pt)])
        end_str = ' '.join([str(x) for x in make_ints_if_possible(self.end_pt)])
        # pos_str = f"{self.start_pt[0]} {self.start_pt[1]} {self.start_pt[2]} {self.end_pt[0]} {self.end_pt[1]} {self.end_pt[2]}"
        vec = ada.Direction(self.end_pt - self.start_pt)
        length = vec.get_length()
        s1 = 0
        s2 = make_ints_if_possible([length])[0]
        return f"-{self.entity_id} edge $-1 -1 -1 $-1 ${self.vertex_start_id} {s1} ${self.vertex_end_id} {s2} ${self.coedge_id} ${self.straight_curve_id} forward @7 unknown T {start_str} {end_str} #"

@dataclass
class StraightCurve(SATEntity):
    start_pt: ada.Point
    direction: ada.Direction

    def to_string(self) -> str:
        start_str = ' '.join([str(x) for x in make_ints_if_possible(self.start_pt)])
        direction_str = ' '.join([str(x) for x in make_ints_if_possible(self.direction.get_normalized())])
        return f"-{self.entity_id} straight-curve $-1 -1 -1 $-1 {start_str} {direction_str} I I #"

@dataclass
class PlaneSurface(SATEntity):
    centroid: ada.Point
    normal: ada.Direction
    xvec: ada.Direction


    def to_string(self) -> str:
        centroid_str = ' '.join([str(x) for x in make_ints_if_possible(self.centroid)])
        normal_str = ' '.join([str(x) for x in make_ints_if_possible(self.normal)])
        xvec_str = ' '.join([str(x) for x in make_ints_if_possible(self.xvec)])
        return f"-{self.entity_id} plane-surface $-1 -1 -1 $-1 {centroid_str} {normal_str} {xvec_str} forward_v I I I I #"

@dataclass
class StringAttribName(SATEntity):
    name: str
    face_id: int
    cache_attrib_id: int = -1

    def to_string(self) -> str:
        return f"-{self.entity_id} string_attrib-name_attrib-gen-attrib $-1 -1 ${self.cache_attrib_id} $-1 ${self.face_id} 2 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 1 1 @6 dnvscp @12 {self.name} #"

@dataclass
class CachedPlaneAttribute(SATEntity):
    face_id: int
    name_id: int
    centroid: ada.Point
    normal: ada.Direction

    def to_string(self) -> str:
        centroid_str = ' '.join([str(x) for x in make_ints_if_possible(self.centroid)])
        normal_str = ' '.join([str(x) for x in make_ints_if_possible(self.normal)])
        return f"-{self.entity_id} CachedPlaneAttribute-DNV-attrib $-1 -1 $-1 ${self.name_id} ${self.face_id} 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 {centroid_str} {normal_str} 1 #"