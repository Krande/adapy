from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import ada
import ada.geom.direction
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
    lump: Lump
    bbox: list[float]
    wire: Wire = None

    def to_string(self) -> str:
        # ACIS `shell` record: $next_shell $subshell $first_face $first_wire $lump.
        # The wire pointer heads the chain of wire bodies — the edges that bound
        # no face (a beam with no plate under its axis).
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        face = -1 if self.face is None else self.face.id
        wire = -1 if self.wire is None else self.wire.id
        return f"-{self.id} shell $-1 -1 -1 $-1 $-1 $-1 ${face} ${wire} ${self.lump.id} T {bbox_str} #"


@dataclass
class Wire(SATEntity):
    """A connected collection of edges that bound no face (SAT v4.0 ch.7).

    Genie emits one per group of beams whose axes lie on no plate, hung off the
    shell's wire pointer, so those beams still have ACIS geometry to reference.
    """

    coedge: CoEdge
    shell: Shell
    bbox: list[float]
    next_wire: Wire = None

    def to_string(self) -> str:
        # $next_wire $first_coedge $body_or_shell $subshell <containment>
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        next_wire = -1 if self.next_wire is None else self.next_wire.id
        return (
            f"-{self.id} wire $-1 -1 -1 $-1 ${next_wire} ${self.coedge.id} " f"${self.shell.id} $-1 out T {bbox_str} #"
        )


@dataclass
class Face(SATEntity):
    loop: Loop
    shell: Shell
    name: StringAttribName
    surface: PlaneSurface
    next_face: Face = None

    def to_string(self) -> str:
        # ACIS `face` record (SAT v4.0 spec, ch.6): after the common ENTITY prefix
        # ($attrib -1 -1 $owner) come next_face_in_shell, first_loop, shell,
        # subshell, surface. A shell holds ONE face pointer; the rest of its faces
        # are reached by following next_face, so the chain must be linked.
        next_face = -1 if self.next_face is None else self.next_face.id
        return (
            f"-{self.id} face ${self.name.id} -1 -1 $-1 ${next_face} ${self.loop.id} "
            f"${self.shell.id} $-1 ${self.surface.id} forward double out F F #"
        )


@dataclass
class Loop(SATEntity):
    coedge: CoEdge
    bbox: list[float]
    face: Face = None
    next_loop: Loop = None
    periphery_plane: PlaneSurface = None

    def to_string(self) -> str:
        # ACIS `loop` record: $next_loop $first_coedge $face — the last field is
        # the face this loop bounds, NOT a constant. It used to be hardcoded to
        # `$3`, which only ever happened to be right for a single-plate model
        # (whose face is entity 3); every loop of every other model pointed at
        # the wrong entity.
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        periphery = "unknown"
        if self.periphery_plane is not None:
            periphery = f"periphery ${self.periphery_plane.id} F"
        face_ref = -1 if self.face is None else self.face.id
        # A face points at its first loop only; any hole loops hang off
        # next_loop (an imprint can enclose a region and produce them).
        next_loop = -1 if self.next_loop is None else self.next_loop.id
        return (
            f"-{self.id} loop $-1 -1 -1 $-1 ${next_loop} ${self.coedge.id} ${face_ref} " f"T {bbox_str} {periphery} #"
        )


@dataclass
class Vertex(SATEntity):
    edge: Edge
    point: SatPoint
    attrib: VertEdgeAttribute = None

    def to_string(self) -> str:
        # A vertex names one of its edges — but only when that names the vertex
        # unambiguously. Where its edges fall into separable regions there is no
        # single right answer, so the pointer goes null and a VertEdgeAttribute
        # names one edge per region instead (as Genie writes it).
        attrib = -1 if self.attrib is None else self.attrib.id
        edge = -1 if self.edge is None else self.edge.id
        return f"-{self.id} vertex ${attrib} -1 -1 $-1 ${edge} ${self.point.id} #"


@dataclass
class VertEdgeAttribute(SATEntity):
    """One edge pointer per separable manifold region at a non-manifold vertex.

    SAT v4.0 ch.7 ``vertedge`` (ATTRIB_VERTEDGE : ATTRIB_SYS : ATTRIB): "Contains
    a list of edge pointers ... At nonmanifold vertices, there should be a
    pointer to an edge in each separable manifold region."

    Where a beam's axis runs off the plate it lies on, the vertex at the plate
    boundary carries both the plate's face edges and the wire edge for the free
    run. Nothing joins those two regions, so ACIS cannot reach one from the
    other and the model fails verification with "vertex has edge in multiple
    groups" unless they are declared here.
    """

    vertex: Vertex
    edges: list[Edge]

    # Genie always writes four slots, padding with $-1, whatever the region
    # count — matched rather than trimmed to len(edges), since this is an ACIS
    # system attribute and its own exports are the only worked example.
    slots: int = 4

    def to_string(self) -> str:
        slots = max(self.slots, len(self.edges))
        refs = [f"${e.id}" for e in self.edges] + ["$-1"] * (slots - len(self.edges))
        return (
            f"-{self.id} vertedge-sys-attrib $-1 -1 $-1 $-1 ${self.vertex.id} "
            f"1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 {slots} {' '.join(refs)} #"
        )


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
    loop: Loop | Wire  # a coedge is owned by a loop, or by a wire when it bounds no face
    orientation: Literal["forward", "reversed"]
    partner: CoEdge = None

    def to_string(self) -> str:
        # ACIS `coedge` record: $next_in_loop $prev_in_loop $next_coedge_on_edge
        # $edge <sense> $loop $pcurve. The third pointer is the partner ring: all
        # coedges lying on the same edge form a circular list through it. An edge
        # used by a single face leaves it $-1 (as Genie writes it); an edge shared
        # by two faces links the pair to each other.
        partner = -1 if self.partner is None else self.partner.id
        return (
            f"-{self.id} coedge $-1 -1 -1 $-1 ${self.next_coedge.id} ${self.prev_coedge.id} "
            f"${partner} ${self.edge.id} {self.orientation} ${self.loop.id} $-1 #"
        )


@dataclass
class Edge(SATEntity):
    vertex_start: Vertex
    vertex_end: Vertex
    coedge: CoEdge
    straight_curve: StraightCurve

    start_pt: ada.Point
    end_pt: ada.Point
    attrib_name: StringAttribName = None

    def to_string(self) -> str:
        attrib_ref = "-1"
        if self.attrib_name:
            attrib_ref = self.attrib_name.id
        # The trailing `T <box>` is a bounding box, so it must run min-corner then
        # max-corner. Emitting start_pt/end_pt verbatim inverted the box for any
        # edge running backwards along an axis.
        lo = [min(a, b) for a, b in zip(self.start_pt, self.end_pt)]
        hi = [max(a, b) for a, b in zip(self.start_pt, self.end_pt)]
        bbox_str = " ".join(str(x) for x in make_ints_if_possible([*lo, *hi]))
        vec = ada.Direction(self.end_pt - self.start_pt)
        length = vec.get_length()
        s1 = 0
        s2 = make_ints_if_possible([length])[0]
        return (
            f"-{self.id} edge ${attrib_ref} -1 -1 $-1 ${self.vertex_start.id} {s1} "
            f"${self.vertex_end.id} {s2} ${self.coedge.id} ${self.straight_curve.id} "
            f"forward @7 unknown T {bbox_str} #"
        )


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
    attrib_ref: CachedPlaneAttribute | FusedFaceAttribute | FusedEdgeAttribute = None

    def to_string(self) -> str:
        cache_attrib = -1 if self.attrib_ref is None else self.attrib_ref.id
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
        if isinstance(self.entity, int):
            entity = self.entity
        else:
            entity = self.entity.id
        return f"-{self.id} CachedPlaneAttribute-DNV-attrib $-1 -1 $-1 ${self.name.id} ${entity} 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 {centroid_str} {normal_str} 1 #"


@dataclass
class PositionAttribName(SATEntity):
    position_attrib: PositionAttribName
    fused_face_attrib: FusedFaceAttribute
    face: Face
    face_bbox: list[float]
    box_attrib: Literal["ExactBoxLow", "ExactBoxHigh"]

    def to_string(self) -> str:
        if self.box_attrib == "ExactBoxLow":
            box_attrib = "@11 ExactBoxLow " + " ".join([str(x) for x in self.face_bbox[:3]])
        else:
            box_attrib = "@12 ExactBoxHigh " + " ".join([str(x) for x in self.face_bbox[3:]])

        return f"-{self.id} position_attrib-name_attrib-gen-attrib $-1 -1 ${self.position_attrib.id} ${self.fused_face_attrib.id} ${self.face.id} 2 0 0 0 1 1 1 1 1 1 1 1 1 1 0 1 1 1 {box_attrib} #"


@dataclass
class FusedFaceAttribute(SATEntity):
    name: StringAttribName
    posattrib: PositionAttribName
    face: Face

    def to_string(self) -> str:
        return f"-{self.id} FusedFaceAttribute-DNV-attrib $-1 -1 ${self.posattrib.id} ${self.name.id} ${self.face.id} 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 F 1 0 0 #"


@dataclass
class FusedEdgeAttribute(SATEntity):
    name: StringAttribName
    entity: SATEntity
    edge_idx: int
    edge_seq: tuple[int, int]
    edge_length: int | float

    def to_string(self) -> str:
        length = make_ints_if_possible([self.edge_length])[0]
        edge_spec = f"{self.edge_seq[0]} {self.edge_seq[1]} {self.edge_idx} 0 {length}"
        return f"-{self.id} FusedEdgeAttribute-DNV-attrib $-1 -1 $-1 ${self.name.id} ${self.entity.id} 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 1 {edge_spec} #"
