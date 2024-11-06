from dataclasses import dataclass

import ada


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
    bbox: list[float]

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        return f"-{self.entity_id} lump $-1 -1 -1 $-1 $-1 ${self.shell_id} $0 T {bbox_str} #"


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
    lump_id: int
    name_id: int
    surface_id: int

    def to_string(self) -> str:
        return f"-{self.entity_id} face ${self.name_id} -1 -1 $-1 $-1 ${self.loop_id} ${self.lump_id} $-1 ${self.surface_id} forward double out F F #"


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
class Point(SATEntity):
    x: float
    y: float
    z: float

    def to_string(self) -> str:
        return f"-{self.entity_id} point $-1 -1 -1 $-1 {self.x} {self.y} {self.z} #"

@dataclass
class CoEdge(SATEntity):
    edge_id: int
    loop_id: int
    orientation: int

    def to_string(self) -> str:
        return f"-{self.entity_id} coedge $-1 -1 -1 $-1 ${self.edge_id} ${self.edge_id} $-1 ${self.loop_id} {self.orientation} #"

@dataclass
class Edge(SATEntity):
    vertex_start_id: int
    vertex_end_id: int

    start_pt: ada.Point
    end_pt: ada.Point

    def to_string(self) -> str:
        pos_str = f"{self.start_pt[0]} {self.start_pt[1]} {self.start_pt[2]} {self.end_pt[0]} {self.end_pt[1]} {self.end_pt[2]}"
        vec = ada.Direction(self.end_pt - self.start_pt)
        length = vec.get_length()
        s1 = 0
        s2 = length
        return f"-{self.entity_id} edge $-1 -1 -1 $-1 ${self.vertex_start_id} {s1} ${self.vertex_end_id} {s2} {self.vertex_start_id} {self.vertex_end_id} forward @7 unknown T {pos_str} #"

@dataclass
class PlaneSurface(SATEntity):
    centroid: ada.Point
    zvec: ada.Direction
    xvec: ada.Direction

    def to_string(self) -> str:
        centroid_str = f"{self.centroid[0]} {self.centroid[1]} {self.centroid[2]}"
        zvec_str = f"{self.zvec[0]} {self.zvec[1]} {self.zvec[2]}"
        xvec_str = f"{self.xvec[0]} {self.xvec[1]} {self.xvec[2]}"
        return f"-{self.entity_id} plane-surface $-1 -1 -1 $-1 {centroid_str} {zvec_str} {xvec_str} forward_v I I I I #"

@dataclass
class StringAttribName(SATEntity):
    name: str
    face_id: int

    def to_string(self) -> str:
        return f"-{self.entity_id} string_attrib-name_attrib-gen-attrib $-1 -1 $-1 $-1 ${self.face_id} 2 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 1 1 @6 dnvscp @12 {self.name} #"